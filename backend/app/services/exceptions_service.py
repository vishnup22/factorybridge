import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.models import Artifact, ManufacturingException, Project, User
from app.models.base import utcnow
from app.models.enums import ArtifactStatus, ExceptionStatus, Role, Severity
from app.services import audit

OPEN_STATES = (ExceptionStatus.OPEN.value, ExceptionStatus.IN_REVIEW.value)
NON_WAIVABLE_REASONS = frozenset(
    {"VOLTAGE_COMPATIBILITY_CHANGED", "MOUNTING_INTERFACE_CHANGED", "COMPATIBILITY_UNKNOWN"}
)


def dedup_key_for_node(project_id: uuid.UUID, node_key: str, reason_code: str) -> str:
    return f"node:{project_id}:{node_key}:{reason_code}"


def upsert_exception(
    db: Session,
    project: Project,
    *,
    dedup_key: str,
    reason_code: str,
    severity: str,
    description: str,
    required_action: str,
    evidence: dict[str, Any],
    actor: User | None,
    change_id: uuid.UUID | None = None,
    node_key: str | None = None,
    artifact_id: uuid.UUID | None = None,
    extraction_run_id: uuid.UUID | None = None,
) -> tuple[ManufacturingException, str]:
    """Create, refresh, or reopen an exception keyed by dedup_key. Returns (row, outcome) where
    outcome is CREATED | UNCHANGED | UPDATED | REOPENED. Replaying the same trigger is a no-op."""
    existing = db.execute(
        select(ManufacturingException).where(ManufacturingException.dedup_key == dedup_key).with_for_update()
    ).scalar_one_or_none()
    trigger = {"change_id": str(change_id) if change_id else None, "at": utcnow().isoformat()}
    if existing is None:
        exc = ManufacturingException(
            organization_id=project.organization_id,
            project_id=project.id,
            change_id=change_id,
            artifact_id=artifact_id,
            extraction_run_id=extraction_run_id,
            node_key=node_key,
            dedup_key=dedup_key,
            reason_code=reason_code,
            severity=severity,
            status=ExceptionStatus.OPEN.value,
            description=description,
            required_action=required_action,
            evidence_json={**evidence, "history": [trigger]},
        )
        db.add(exc)
        db.flush()
        outcome = "CREATED"
    elif existing.status in OPEN_STATES:
        if existing.change_id == change_id and change_id is not None:
            return existing, "UNCHANGED"
        history = list((existing.evidence_json or {}).get("history", [])) + [trigger]
        existing.change_id = change_id or existing.change_id
        existing.artifact_id = artifact_id or existing.artifact_id
        existing.severity = severity
        existing.description = description
        existing.evidence_json = {**evidence, "history": history}
        exc, outcome = existing, "UPDATED"
    else:
        history = list((existing.evidence_json or {}).get("history", [])) + [trigger]
        before = {"status": existing.status, "resolved_by": str(existing.resolved_by)}
        existing.status = ExceptionStatus.OPEN.value
        existing.reopened_count += 1
        existing.change_id = change_id
        existing.artifact_id = artifact_id
        existing.severity = severity
        existing.description = description
        existing.evidence_json = {**evidence, "history": history}
        existing.resolved_by = None
        existing.resolved_at = None
        existing.resolution_note = None
        existing.resolution_artifact_id = None
        exc, outcome = existing, "REOPENED"
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor=actor,
            action="EXCEPTION_REOPENED",
            resource_type="exception",
            resource_id=exc.id,
            before=before,
            after={"status": exc.status, "reason_code": reason_code, "change_id": str(change_id)},
        )
        return exc, outcome
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="EXCEPTION_OPENED" if outcome == "CREATED" else "EXCEPTION_UPDATED",
        resource_type="exception",
        resource_id=exc.id,
        after={
            "reason_code": reason_code,
            "severity": severity,
            "node_key": node_key,
            "change_id": str(change_id) if change_id else None,
        },
    )
    return exc, outcome


def get_exception(db: Session, project: Project, exception_id: uuid.UUID, lock: bool = False) -> ManufacturingException:
    stmt = select(ManufacturingException).where(
        ManufacturingException.id == exception_id, ManufacturingException.project_id == project.id
    )
    if lock:
        stmt = stmt.with_for_update()
    exc = db.execute(stmt).scalar_one_or_none()
    if exc is None:
        raise NotFound("Exception not found", code="EXCEPTION_NOT_FOUND")
    return exc


def mark_resolved(
    db: Session,
    project: Project,
    exc: ManufacturingException,
    actor: User,
    note: str,
    artifact: Artifact | None,
) -> None:
    before = {"status": exc.status}
    exc.status = ExceptionStatus.RESOLVED.value
    exc.resolved_by = actor.id
    exc.resolved_at = utcnow()
    exc.resolution_note = note
    exc.resolution_artifact_id = artifact.id if artifact else None
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="EXCEPTION_RESOLVED",
        resource_type="exception",
        resource_id=exc.id,
        before=before,
        after={
            "status": exc.status,
            "reason_code": exc.reason_code,
            "resolution_artifact_id": str(artifact.id) if artifact else None,
            "resolution_artifact_revision": artifact.revision if artifact else None,
            "note": note,
        },
    )


def assert_resolution_artifact(exc: ManufacturingException, artifact: Artifact, invalidated: Artifact | None) -> None:
    if artifact.node_key != exc.node_key:
        raise ValidationFailed("Artifact belongs to a different dependency node", code="ARTIFACT_NODE_MISMATCH")
    if not artifact.is_current or artifact.status != ArtifactStatus.VALID.value:
        raise ValidationFailed(
            "Resolution requires a current, deterministically VALID artifact revision",
            code="RESOLUTION_EVIDENCE_INVALID",
            extra={"artifact_status": artifact.status, "validation": artifact.validation_json},
        )
    if invalidated is not None and artifact.revision <= invalidated.revision:
        raise ValidationFailed(
            "Resolution requires a NEW artifact revision created after the invalidation",
            code="RESOLUTION_EVIDENCE_STALE",
        )


def waive(db: Session, project: Project, exc: ManufacturingException, actor: User, rationale: str) -> None:
    if actor.role != Role.APPROVER.value:
        raise Forbidden("Only an approver can waive an exception", code="ROLE_FORBIDDEN")
    if exc.status not in OPEN_STATES:
        raise Conflict("Exception is not open", code="EXCEPTION_NOT_OPEN")
    if exc.severity == Severity.CRITICAL.value or exc.reason_code in NON_WAIVABLE_REASONS:
        raise ValidationFailed("Critical and hard-rule compatibility exceptions cannot be waived", code="NOT_WAIVABLE")
    if len(rationale.strip()) < 10:
        raise ValidationFailed("Waiver requires a rationale (min 10 chars)", code="RATIONALE_REQUIRED")
    exc.status = ExceptionStatus.WAIVED.value
    exc.resolved_by = actor.id
    exc.resolved_at = utcnow()
    exc.resolution_note = f"WAIVED: {rationale}"
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="EXCEPTION_WAIVED",
        resource_type="exception",
        resource_id=exc.id,
        before={"status": "OPEN"},
        after={"status": exc.status, "rationale": rationale},
    )


def open_for_node(db: Session, project: Project, node_key: str) -> list[ManufacturingException]:
    return list(
        db.execute(
            select(ManufacturingException)
            .where(
                ManufacturingException.project_id == project.id,
                ManufacturingException.node_key == node_key,
                ManufacturingException.status.in_(OPEN_STATES),
            )
            .with_for_update()
        ).scalars()
    )
