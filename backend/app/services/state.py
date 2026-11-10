"""Loading persisted project state into pure domain types, baseline hashing, readiness recompute,
and atomic baseline invalidation (Invariant I-2)."""

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain.baseline import BaselineInputs, baseline_hash, params_hash
from app.domain.bom import BomLine
from app.domain.graph import Edge, node_type
from app.domain.readiness import Readiness, ReadinessFacts, compute_readiness
from app.domain.rules import Rule
from app.models import (
    ApprovalRequest,
    Artifact,
    BomItem,
    BomRevision,
    CompatibilityRule,
    DependencyEdge,
    Document,
    ManufacturingException,
    Project,
    User,
)
from app.models.base import utcnow
from app.models.enums import ApprovalStatus, DocumentStatus, ExceptionStatus
from app.services import audit

PENDING_DOC_STATES = (DocumentStatus.PENDING, DocumentStatus.PROCESSING, DocumentStatus.REVIEW_REQUIRED)


def item_to_line(item: BomItem) -> BomLine:
    return BomLine(
        line_key=item.line_key,
        part_number=item.part_number,
        description=item.description,
        quantity=Decimal(item.quantity),
        unit=item.unit,
        part_revision=item.part_revision,
        attributes=dict(item.attributes_json or {}),
    )


def load_lines(db: Session, revision_id: uuid.UUID | None) -> list[BomLine]:
    if revision_id is None:
        return []
    items = db.execute(
        select(BomItem).where(BomItem.bom_revision_id == revision_id).order_by(BomItem.line_key)
    ).scalars()
    return [item_to_line(i) for i in items]


def current_revision(db: Session, project: Project) -> BomRevision | None:
    if project.current_bom_revision_id is None:
        return None
    return db.get(BomRevision, project.current_bom_revision_id)


def current_artifacts(db: Session, project_id: uuid.UUID) -> dict[str, Artifact]:
    rows = db.execute(
        select(Artifact).where(Artifact.project_id == project_id, Artifact.is_current.is_(True))
    ).scalars()
    return {a.node_key: a for a in rows}


def active_edges(db: Session, project_id: uuid.UUID) -> list[Edge]:
    rows = db.execute(
        select(DependencyEdge).where(DependencyEdge.project_id == project_id, DependencyEdge.is_active.is_(True))
    ).scalars()
    return [Edge(r.source_node_key, r.target_node_key, r.dependency_type, r.rule_code) for r in rows]


def project_rules(db: Session, project_id: uuid.UUID) -> dict[str, Rule]:
    rows = db.execute(select(CompatibilityRule).where(CompatibilityRule.project_id == project_id)).scalars()
    return {
        r.rule_code: Rule(r.rule_code, r.check_kind, dict(r.params_json), r.is_critical, r.description) for r in rows
    }


def baseline_inputs(db: Session, project: Project) -> BaselineInputs:
    rev = current_revision(db, project)
    arts = current_artifacts(db, project.id)
    rules = db.execute(select(CompatibilityRule).where(CompatibilityRule.project_id == project.id)).scalars()
    return BaselineInputs(
        bom_revision_id=str(rev.id) if rev else None,
        bom_content_hash=rev.content_hash if rev else None,
        artifacts=[(a.node_key, str(a.id), a.revision, a.content_hash, a.status) for a in arts.values()],
        edges=[(e.source, e.target, e.dependency_type, e.rule_code) for e in active_edges(db, project.id)],
        rules=[(r.rule_code, r.check_kind, params_hash(dict(r.params_json))) for r in rules],
        rule_version=get_settings().rule_version,
    )


def current_baseline_hash(db: Session, project: Project) -> str:
    return baseline_hash(baseline_inputs(db, project))


def required_nodes(edges: list[Edge]) -> list[str]:
    nodes = {e.source for e in edges} | {e.target for e in edges}
    return sorted(n for n in nodes if node_type(n) not in ("BOM_LINE", "READINESS_APPROVAL"))


def readiness_facts(db: Session, project: Project) -> ReadinessFacts:
    edges = active_edges(db, project.id)
    arts = current_artifacts(db, project.id)
    pending = db.execute(
        select(func.count())
        .select_from(Document)
        .where(Document.project_id == project.id, Document.processing_status.in_(PENDING_DOC_STATES))
    ).scalar_one()
    open_exc = db.execute(
        select(ManufacturingException.id, ManufacturingException.severity, ManufacturingException.reason_code).where(
            ManufacturingException.project_id == project.id,
            ManufacturingException.status.in_((ExceptionStatus.OPEN, ExceptionStatus.IN_REVIEW)),
        )
    ).all()
    approved = db.execute(
        select(ApprovalRequest.id, ApprovalRequest.baseline_hash, ApprovalRequest.baseline_version).where(
            ApprovalRequest.project_id == project.id, ApprovalRequest.status == ApprovalStatus.APPROVED
        )
    ).all()
    return ReadinessFacts(
        has_bom=project.current_bom_revision_id is not None,
        pending_extractions=int(pending),
        graph_node_count=len(edges),
        required_nodes=required_nodes(edges),
        artifact_status={k: a.status for k, a in arts.items()},
        open_exceptions=[(str(i), s, r) for i, s, r in open_exc],
        current_baseline_hash=current_baseline_hash(db, project),
        current_baseline_version=project.baseline_version,
        approved=[(str(i), h, v) for i, h, v in approved],
    )


def evaluate_readiness(db: Session, project: Project) -> Readiness:
    db.flush()
    return compute_readiness(readiness_facts(db, project))


def recompute_status(db: Session, project: Project, actor: User | None, cause: str) -> Readiness:
    """Persist derived readiness. READY can only result from a valid approval for the exact
    current baseline (I-1); it is never set directly by callers."""
    readiness = evaluate_readiness(db, project)
    if project.status != readiness.status:
        before = project.status
        project.status = readiness.status
        project.row_version += 1
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor=actor,
            action="PROJECT_READINESS_CHANGED",
            resource_type="project",
            resource_id=project.id,
            before={"status": before},
            after={
                "status": readiness.status,
                "cause": cause,
                "blockers": [b.code for b in readiness.blockers],
            },
        )
    return readiness


def invalidate_baseline(db: Session, project: Project, actor: User | None, cause: str) -> int:
    """Called inside the same transaction as any baseline-input change. Increments the baseline
    version and stale-marks every pending/approved approval. Returns the number staled."""
    project.baseline_version += 1
    project.row_version += 1
    approvals = (
        db.execute(
            select(ApprovalRequest)
            .where(
                ApprovalRequest.project_id == project.id,
                ApprovalRequest.status.in_((ApprovalStatus.PENDING, ApprovalStatus.APPROVED)),
            )
            .with_for_update()
        )
        .scalars()
        .all()
    )
    now = utcnow()
    for a in approvals:
        before = a.status
        a.status = ApprovalStatus.STALE.value
        a.stale_reason = cause
        a.staled_at = now
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor=actor,
            action="APPROVAL_STALED",
            resource_type="approval_request",
            resource_id=a.id,
            before={"status": before, "baseline_hash": a.baseline_hash, "baseline_version": a.baseline_version},
            after={"status": a.status, "cause": cause, "new_baseline_version": project.baseline_version},
        )
    return len(approvals)
