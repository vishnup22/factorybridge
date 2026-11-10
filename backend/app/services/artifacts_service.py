import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound, ValidationFailed
from app.domain.artifacts import (
    ArtifactFacts,
    SupplierFacts,
    ValidationContext,
    artifact_content_hash,
    validate_artifact,
)
from app.domain.graph import node_type, traverse_downstream, validate_node_key
from app.domain.impact import REQUIRED_ACTIONS
from app.models import Artifact, ManufacturingException, Project, SupplierOption, User
from app.models.enums import ArtifactStatus, ArtifactType
from app.services import audit, exceptions_service, state

STALE_REASON = {
    "SUPPLIER_SELECTION": "SOURCING_REVALIDATION_REQUIRED",
    "WORK_INSTRUCTION": "WORK_INSTRUCTION_STALE",
    "QC_PLAN": "QC_PLAN_STALE",
}


def build_context(db: Session, project: Project) -> ValidationContext:
    lines = {line.line_key: line for line in state.load_lines(db, project.current_bom_revision_id)}
    suppliers = db.execute(
        select(SupplierOption).where(SupplierOption.organization_id == project.organization_id)
    ).scalars()
    arts = state.current_artifacts(db, project.id)
    return ValidationContext(
        lines=lines,
        target_quantity=project.target_quantity,
        suppliers={
            str(s.id): SupplierFacts(
                str(s.id),
                s.part_number,
                s.part_revision,
                s.vendor_name,
                s.available_qty,
                s.lead_days,
                s.approved_for_part,
            )
            for s in suppliers
        },
        rules=state.project_rules(db, project.id),
        artifacts={k: ArtifactFacts(k, a.revision, a.status) for k, a in arts.items()},
    )


def resolve_supplier_code(db: Session, project: Project, content: dict[str, Any]) -> dict[str, Any]:
    """Allow clients to reference a supplier by its catalog option_code (e.g. SUP-200)."""
    code = content.get("supplier_option_code")
    if code and not content.get("supplier_option_id"):
        opt = db.execute(
            select(SupplierOption).where(
                SupplierOption.organization_id == project.organization_id, SupplierOption.option_code == code
            )
        ).scalar_one_or_none()
        if opt is None:
            raise ValidationFailed(f"Unknown supplier option {code}", code="SUPPLIER_OPTION_MISSING")
        content = {**content, "supplier_option_id": str(opt.id)}
    return content


def create_artifact_revision(
    db: Session,
    project: Project,
    actor: User,
    *,
    node_key: str,
    content: dict[str, Any],
    title: str = "",
    expected_current_revision: int | None = None,
    cause: str = "ARTIFACT_REVISED",
) -> Artifact:
    """Create a new artifact revision, validated deterministically against the current BOM.
    Caller holds the project lock. Invalidates the baseline and re-checks downstream artifacts."""
    if project.current_bom_revision_id is None:
        raise ValidationFailed("Publish a BOM revision before creating artifacts", code="NO_BOM_REVISION")
    if not validate_node_key(node_key) or node_type(node_key) not in {t.value for t in ArtifactType}:
        raise ValidationFailed(f"Invalid artifact node key {node_key!r}", code="INVALID_NODE_KEY")
    artifact_type = node_type(node_key)
    if artifact_type == "COMPATIBILITY_CHECK":
        content = {"rule_code": node_key.split(":", 1)[1]}
    if artifact_type == "SUPPLIER_SELECTION":
        content = resolve_supplier_code(db, project, content)
        content.setdefault("line_key", node_key.split(":", 1)[1])
    current = db.execute(
        select(Artifact)
        .where(Artifact.project_id == project.id, Artifact.node_key == node_key, Artifact.is_current.is_(True))
        .with_for_update()
    ).scalar_one_or_none()
    if expected_current_revision is not None:
        actual = current.revision if current else 0
        if actual != expected_current_revision:
            raise Conflict(
                f"Artifact is at revision {actual}, expected {expected_current_revision}",
                code="STALE_ARTIFACT_REVISION",
            )
    ctx = build_context(db, project)
    result = validate_artifact(artifact_type, content, ctx)
    stored = result.computed_content if result.computed_content is not None else content
    max_rev = db.execute(
        select(func.max(Artifact.revision)).where(Artifact.project_id == project.id, Artifact.node_key == node_key)
    ).scalar_one()
    if current is not None:
        current.is_current = False
        current.status = ArtifactStatus.SUPERSEDED.value
        db.flush()
    rev = state.current_revision(db, project)
    assert rev is not None
    art = Artifact(
        organization_id=project.organization_id,
        project_id=project.id,
        node_key=node_key,
        artifact_type=artifact_type,
        revision=int(max_rev or 0) + 1,
        title=title or (current.title if current else node_key),
        status=ArtifactStatus.VALID.value if result.valid else ArtifactStatus.INVALID.value,
        is_current=True,
        content_json=stored,
        content_hash=artifact_content_hash(stored),
        based_on_hash=rev.content_hash,
        validation_json={**result.as_dict(), "validated_against_bom_revision": rev.revision_number},
        created_by=actor.id,
    )
    db.add(art)
    db.flush()
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="ARTIFACT_REVISION_CREATED",
        resource_type="artifact",
        resource_id=art.id,
        before={"revision": current.revision, "status": "SUPERSEDED"} if current else None,
        after={
            "node_key": node_key,
            "revision": art.revision,
            "status": art.status,
            "findings": [f["code"] for f in result.as_dict()["findings"]],
            "based_on_bom_revision": f"R{rev.revision_number}",
        },
    )
    state.invalidate_baseline(db, project, actor, f"{cause}:{node_key}:r{art.revision}")
    propagate_artifact_change(db, project, actor, node_key)
    return art


def propagate_artifact_change(db: Session, project: Project, actor: User, node_key: str) -> list[str]:
    """After an artifact revision, re-validate downstream artifacts reachable via dependency edges.
    Any that no longer validate (e.g. a QC plan pinned to the old work-instruction revision) become
    REVALIDATION_REQUIRED and get an exception."""
    edges = state.active_edges(db, project.id)
    reaches = traverse_downstream(edges, [node_key])
    ctx = build_context(db, project)
    arts = state.current_artifacts(db, project.id)
    flagged: list[str] = []
    for r in reaches:
        art = arts.get(r.node)
        if art is None or art.status != ArtifactStatus.VALID.value:
            continue
        result = validate_artifact(art.artifact_type, art.content_json, ctx)
        if result.valid:
            continue
        art.status = ArtifactStatus.REVALIDATION_REQUIRED.value
        reason = STALE_REASON.get(art.artifact_type, "COMPATIBILITY_UNKNOWN")
        exceptions_service.upsert_exception(
            db,
            project,
            dedup_key=exceptions_service.dedup_key_for_node(project.id, r.node, reason),
            reason_code=reason,
            severity="HIGH",
            description=f"{r.node} r{art.revision} no longer validates after {node_key} changed",
            required_action=REQUIRED_ACTIONS.get(reason, "Revise artifact"),
            evidence={"path": list(r.path), "findings": [f.as_dict() for f in result.findings]},
            actor=actor,
            node_key=r.node,
            artifact_id=art.id,
        )
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor=actor,
            action="ARTIFACT_INVALIDATED",
            resource_type="artifact",
            resource_id=art.id,
            before={"status": "VALID"},
            after={"status": art.status, "upstream": node_key, "path": list(r.path)},
        )
        flagged.append(r.node)
    return flagged


def resolve_exception(
    db: Session,
    project: Project,
    actor: User,
    exc: ManufacturingException,
    *,
    note: str,
    content: dict[str, Any] | None,
    artifact_id: uuid.UUID | None,
    expected_current_revision: int | None = None,
) -> tuple[ManufacturingException, Artifact]:
    if exc.status not in exceptions_service.OPEN_STATES:
        raise Conflict("Exception is not open", code="EXCEPTION_NOT_OPEN")
    if len(note.strip()) < 5:
        raise ValidationFailed("A resolution note is required", code="RESOLUTION_NOTE_REQUIRED")
    if exc.node_key is None or node_type(exc.node_key) not in {t.value for t in ArtifactType}:
        raise ValidationFailed(
            "This exception is resolved through its own workflow (extraction review or approval)",
            code="NOT_ARTIFACT_RESOLVABLE",
        )
    invalidated = db.get(Artifact, exc.artifact_id) if exc.artifact_id else None
    if content is None and artifact_id is None:
        raise ValidationFailed(
            "Resolution requires a new artifact revision or a reference to one", code="RESOLUTION_EVIDENCE_REQUIRED"
        )
    if content is not None:
        art = create_artifact_revision(
            db,
            project,
            actor,
            node_key=exc.node_key,
            content=content,
            expected_current_revision=expected_current_revision,
            cause="EXCEPTION_RESOLUTION",
        )
    else:
        found = db.get(Artifact, artifact_id)
        if found is None or found.project_id != project.id:
            raise NotFound("Artifact not found", code="ARTIFACT_NOT_FOUND")
        art = found
    exceptions_service.assert_resolution_artifact(exc, art, invalidated)
    for other in exceptions_service.open_for_node(db, project, exc.node_key):
        exceptions_service.mark_resolved(db, project, other, actor, note, art)
    return exc, art
