import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain.bom import ChangeSetError, apply_change_set
from app.domain.graph import node_type
from app.domain.impact import ImpactResult, assess_impact
from app.models import (
    BomRevision,
    EngineeringChange,
    ImpactAssessment,
    Project,
    User,
)
from app.models.base import utcnow
from app.models.enums import ArtifactStatus, ChangeStatus
from app.services import audit, bom_service, exceptions_service, outbox, state


def _revision_by_number(db: Session, project: Project, number: int) -> BomRevision:
    return bom_service.get_revision(db, project, number)


def _apply(db: Session, base: BomRevision, operations: list[dict[str, Any]]):  # type: ignore[no-untyped-def]
    try:
        return apply_change_set(state.load_lines(db, base.id), operations)
    except ChangeSetError as e:
        raise ValidationFailed(
            "Change set is invalid", code="INVALID_CHANGE_SET", extra={"issues": [i.as_dict() for i in e.issues]}
        )


def propose_change(
    db: Session,
    project: Project,
    actor: User,
    *,
    base_revision_number: int,
    reason: str,
    operations: list[dict[str, Any]],
) -> EngineeringChange:
    current = state.current_revision(db, project)
    if current is None:
        raise ValidationFailed("Project has no BOM revision to change", code="NO_BOM_REVISION")
    if current.revision_number != base_revision_number:
        raise Conflict(
            f"Base revision R{base_revision_number} is not current (current is R{current.revision_number})",
            code="STALE_BASE_REVISION",
        )
    if len(reason.strip()) < 5:
        raise ValidationFailed("A change reason is required", code="REASON_REQUIRED")
    _apply(db, current, operations)  # validate now; re-applied at publish against the then-current base
    count = db.execute(
        select(func.count()).select_from(EngineeringChange).where(EngineeringChange.project_id == project.id)
    ).scalar_one()
    change = EngineeringChange(
        organization_id=project.organization_id,
        project_id=project.id,
        code=f"EC-{int(count) + 1:03d}",
        from_bom_revision_id=current.id,
        submitted_by=actor.id,
        reason=reason.strip(),
        status=ChangeStatus.PROPOSED.value,
        change_set_json=operations,
    )
    db.add(change)
    db.flush()
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="CHANGE_PROPOSED",
        resource_type="engineering_change",
        resource_id=change.id,
        after={"code": change.code, "base_revision": f"R{base_revision_number}", "operations": operations},
    )
    return change


def get_change(db: Session, project: Project, change_id: uuid.UUID, lock: bool = False) -> EngineeringChange:
    stmt = select(EngineeringChange).where(
        EngineeringChange.id == change_id, EngineeringChange.project_id == project.id
    )
    if lock:
        stmt = stmt.with_for_update()
    change = db.execute(stmt).scalar_one_or_none()
    if change is None:
        raise NotFound("Engineering change not found", code="CHANGE_NOT_FOUND")
    return change


def preview(db: Session, project: Project, change: EngineeringChange) -> ImpactResult:
    """Compute (without persisting) the impact this change would have against its base revision."""
    base = db.get(BomRevision, change.from_bom_revision_id)
    assert base is not None
    new_lines = _apply(db, base, change.change_set_json)
    return assess_impact(
        state.load_lines(db, base.id),
        new_lines,
        state.active_edges(db, project.id),
        state.project_rules(db, project.id),
        set(state.current_artifacts(db, project.id)),
    )


def publish_change(
    db: Session,
    project: Project,
    actor: User,
    change: EngineeringChange,
    *,
    expected_baseline_version: int | None,
) -> ImpactAssessment:
    """Atomically: new immutable revision + impact assessment + artifact invalidation + exceptions
    + stale approvals + readiness recompute. Caller holds the project row lock."""
    if change.status == ChangeStatus.PUBLISHED.value:
        raise Conflict("Change already published", code="CHANGE_ALREADY_PUBLISHED")
    if change.status != ChangeStatus.PROPOSED.value:
        raise Conflict(f"Change is {change.status}", code="CHANGE_NOT_PUBLISHABLE")
    if change.from_bom_revision_id != project.current_bom_revision_id:
        raise Conflict(
            "The change's base revision is no longer current; re-propose against the latest revision",
            code="STALE_BASE_REVISION",
        )
    if expected_baseline_version is not None and expected_baseline_version != project.baseline_version:
        raise Conflict(
            f"Baseline version is {project.baseline_version}, expected {expected_baseline_version}",
            code="STALE_BASELINE_VERSION",
        )
    base = db.get(BomRevision, change.from_bom_revision_id)
    assert base is not None
    old_lines = state.load_lines(db, base.id)
    new_lines = _apply(db, base, change.change_set_json)
    old_hash = state.current_baseline_hash(db, project)
    edges = state.active_edges(db, project.id)
    rules = state.project_rules(db, project.id)
    arts = state.current_artifacts(db, project.id)
    result = assess_impact(old_lines, new_lines, edges, rules, set(arts))

    new_rev = bom_service.create_revision(
        db, project, new_lines, actor, source_kind="ENGINEERING_CHANGE", source_ref=change.id
    )
    for imp in result.impacted:
        if imp.node_type == "READINESS_APPROVAL":
            continue
        art = arts.get(imp.node)
        if art is not None and art.status != ArtifactStatus.REVALIDATION_REQUIRED.value:
            before = art.status
            art.status = ArtifactStatus.REVALIDATION_REQUIRED.value
            audit.record(
                db,
                organization_id=project.organization_id,
                project_id=project.id,
                actor=actor,
                action="ARTIFACT_INVALIDATED",
                resource_type="artifact",
                resource_id=art.id,
                before={"status": before, "revision": art.revision},
                after={"status": art.status, "change": change.code, "path": list(imp.path)},
            )
        if node_type(imp.node) == "BOM_LINE":
            continue
        exceptions_service.upsert_exception(
            db,
            project,
            dedup_key=exceptions_service.dedup_key_for_node(project.id, imp.node, imp.reason_code),
            reason_code=imp.reason_code,
            severity=imp.severity,
            description=_describe(imp.node, imp.reason_code, change.code, imp.changed_fields),
            required_action=imp.required_action,
            evidence={
                "change_code": change.code,
                "from_revision": f"R{base.revision_number}",
                "to_revision": f"R{new_rev.revision_number}",
                "path": list(imp.path),
                "rule_codes": list(imp.rule_codes),
                "changed_fields": list(imp.changed_fields),
                "evaluation": imp.evaluation,
                "last_validated_baseline_hash": old_hash,
                "invalidated_artifact_revision": art.revision if art else None,
            },
            actor=actor,
            change_id=change.id,
            node_key=imp.node,
            artifact_id=art.id if art else None,
        )
    staled = state.invalidate_baseline(db, project, actor, f"BOM_REVISION_PUBLISHED:R{new_rev.revision_number}")
    change.status = ChangeStatus.PUBLISHED.value
    change.to_bom_revision_id = new_rev.id
    change.published_by = actor.id
    change.published_at = utcnow()
    db.flush()
    new_hash = state.current_baseline_hash(db, project)
    summary = {
        "change_code": change.code,
        "from_revision": f"R{base.revision_number}",
        "to_revision": f"R{new_rev.revision_number}",
        "changed_nodes": result.changed_nodes,
        "impacted_count": len(result.impacted),
        "by_severity": _count_by(result, "severity"),
        "by_reason": _count_by(result, "reason_code"),
        "approvals_staled": staled,
    }
    assessment = ImpactAssessment(
        change_id=change.id,
        project_id=project.id,
        baseline_hash=old_hash,
        new_baseline_hash=new_hash,
        impact_graph_json=result.as_dict(),
        rule_version=get_settings().rule_version,
        summary_json=summary,
    )
    db.add(assessment)
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="CHANGE_PUBLISHED",
        resource_type="engineering_change",
        resource_id=change.id,
        before={"bom_revision": f"R{base.revision_number}", "baseline_hash": old_hash},
        after={**summary, "baseline_hash": new_hash},
    )
    outbox.enqueue(
        db,
        organization_id=project.organization_id,
        event_type="EngineeringChangePublished",
        aggregate_id=change.id,
        payload={"project_id": str(project.id), "change_id": str(change.id), "to_revision": new_rev.revision_number},
    )
    state.recompute_status(db, project, actor, f"CHANGE_PUBLISHED:{change.code}")
    db.flush()
    return assessment


def _count_by(result: ImpactResult, attr: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for i in result.impacted:
        k = getattr(i, attr)
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def _describe(node: str, reason: str, code: str, fields: tuple[str, ...]) -> str:
    changed = ", ".join(fields) if fields else "BOM line"
    return f"{code} changed {changed}; {node} requires revalidation ({reason})."


def withdraw(db: Session, project: Project, actor: User, change: EngineeringChange) -> None:
    if change.status != ChangeStatus.PROPOSED.value:
        raise Conflict("Only proposed changes can be withdrawn", code="CHANGE_NOT_WITHDRAWABLE")
    change.status = ChangeStatus.WITHDRAWN.value
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="CHANGE_WITHDRAWN",
        resource_type="engineering_change",
        resource_id=change.id,
        after={"code": change.code},
    )
