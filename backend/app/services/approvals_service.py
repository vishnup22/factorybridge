"""Revision-bound release approvals (Invariants I-1, I-2, I-3)."""

import uuid
from typing import Literal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, Forbidden, NotFound, ValidationFailed
from app.domain.readiness import compute_blockers
from app.models import ApprovalRequest, BomRevision, EngineeringChange, Project, User
from app.models.base import utcnow
from app.models.enums import ApprovalStatus, ChangeStatus, Role
from app.services import audit, outbox, state


def _blockers_excluding_approval(db: Session, project: Project) -> list[dict[str, object]]:
    return [b.as_dict() for b in compute_blockers(state.readiness_facts(db, project))]


def unreleased_change_authors(db: Session, project: Project) -> set[uuid.UUID]:
    """Users who submitted or published any change after the most recently approved revision."""
    last_approved_rev = db.execute(
        select(BomRevision.revision_number)
        .join(ApprovalRequest, ApprovalRequest.bom_revision_id == BomRevision.id)
        .where(ApprovalRequest.project_id == project.id, ApprovalRequest.decision == "APPROVE")
        .order_by(BomRevision.revision_number.desc())
        .limit(1)
    ).scalar_one_or_none()
    rows = db.execute(
        select(EngineeringChange.submitted_by, EngineeringChange.published_by)
        .join(BomRevision, BomRevision.id == EngineeringChange.to_bom_revision_id)
        .where(
            EngineeringChange.project_id == project.id,
            EngineeringChange.status == ChangeStatus.PUBLISHED.value,
            BomRevision.revision_number > (last_approved_rev or 0),
        )
    ).all()
    authors: set[uuid.UUID] = set()
    for submitted, published in rows:
        authors.add(submitted)
        if published:
            authors.add(published)
    return authors


def _eligible(db: Session, project: Project, candidate: User, requester: User) -> str | None:
    if candidate.role != Role.APPROVER.value or not candidate.is_active:
        return "not an active approver"
    if candidate.organization_id != project.organization_id:
        return "not in project organization"
    if candidate.id == requester.id:
        return "cannot approve own request"
    if candidate.id in unreleased_change_authors(db, project):
        return "authored an unreleased engineering change"
    return None


def request_approval(
    db: Session,
    project: Project,
    actor: User,
    *,
    expected_baseline_version: int,
    assigned_approver_id: uuid.UUID | None,
    note: str,
) -> ApprovalRequest:
    if expected_baseline_version != project.baseline_version:
        raise Conflict(
            f"Baseline version is {project.baseline_version}, request was for {expected_baseline_version}",
            code="STALE_BASELINE_VERSION",
        )
    rev = state.current_revision(db, project)
    if rev is None:
        raise ValidationFailed("No BOM revision to approve", code="NO_BOM_REVISION")
    blockers = _blockers_excluding_approval(db, project)
    if blockers:
        raise ValidationFailed(
            "Baseline is not releasable; resolve blockers first", code="BLOCKERS_PRESENT", extra={"blockers": blockers}
        )
    if assigned_approver_id is not None:
        approver = db.get(User, assigned_approver_id)
        if approver is None or approver.organization_id != project.organization_id:
            raise ValidationFailed("Assigned approver not found", code="APPROVER_NOT_FOUND")
        why = _eligible(db, project, approver, actor)
        if why:
            raise ValidationFailed(f"Assigned approver ineligible: {why}", code="APPROVER_INELIGIBLE")
    else:
        candidates = db.execute(
            select(User)
            .where(User.organization_id == project.organization_id, User.role == Role.APPROVER.value)
            .order_by(User.email)
        ).scalars()
        approver = next((c for c in candidates if _eligible(db, project, c, actor) is None), None)
        if approver is None:
            raise ValidationFailed("No eligible approver available", code="NO_ELIGIBLE_APPROVER")
    inputs = state.baseline_inputs(db, project)
    req = ApprovalRequest(
        organization_id=project.organization_id,
        project_id=project.id,
        bom_revision_id=rev.id,
        baseline_hash=state.current_baseline_hash(db, project),
        baseline_version=project.baseline_version,
        snapshot_json={**inputs.snapshot(), "bom_revision_number": rev.revision_number},
        requested_by=actor.id,
        assigned_approver_id=approver.id,
        status=ApprovalStatus.PENDING.value,
        reason=note,
    )
    db.add(req)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("An approval request is already pending for this project", code="APPROVAL_ALREADY_PENDING")
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="APPROVAL_REQUESTED",
        resource_type="approval_request",
        resource_id=req.id,
        after={
            "bom_revision": f"R{rev.revision_number}",
            "baseline_hash": req.baseline_hash,
            "baseline_version": req.baseline_version,
            "assigned_approver": approver.email,
        },
    )
    outbox.enqueue(
        db,
        organization_id=project.organization_id,
        event_type="ApprovalRequested",
        aggregate_id=req.id,
        payload={"project_id": str(project.id), "approval_id": str(req.id)},
    )
    state.recompute_status(db, project, actor, "APPROVAL_REQUESTED")
    return req


def get_approval(db: Session, project: Project, approval_id: uuid.UUID, lock: bool = False) -> ApprovalRequest:
    stmt = select(ApprovalRequest).where(ApprovalRequest.id == approval_id, ApprovalRequest.project_id == project.id)
    if lock:
        stmt = stmt.with_for_update()
    a = db.execute(stmt).scalar_one_or_none()
    if a is None:
        raise NotFound("Approval request not found", code="APPROVAL_NOT_FOUND")
    return a


def decide(
    db: Session,
    project: Project,
    actor: User,
    approval: ApprovalRequest,
    *,
    decision: Literal["APPROVE", "REJECT"],
    reason: str,
    expected_baseline_hash: str | None,
) -> ApprovalRequest:
    """Caller holds the project lock (FOR UPDATE) and the approval lock. Freshness is re-verified
    against the recomputed baseline inside this transaction, so a concurrent publish either
    commits first (and this fails 409) or waits on the lock (and stales this approval after)."""
    if actor.role != Role.APPROVER.value:
        raise Forbidden("Only an approver can decide release approvals", code="ROLE_FORBIDDEN")
    if approval.assigned_approver_id != actor.id:
        raise Forbidden("You are not the assigned approver for this request", code="NOT_ASSIGNED_APPROVER")
    if approval.requested_by == actor.id or actor.id in unreleased_change_authors(db, project):
        raise Forbidden("Separation of duties: you authored part of this baseline", code="SEPARATION_OF_DUTIES")
    if approval.status == ApprovalStatus.STALE.value:
        raise Conflict(
            "Approval request is stale: the baseline changed after it was requested",
            code="APPROVAL_STALE",
            extra={"stale_reason": approval.stale_reason},
        )
    if approval.status != ApprovalStatus.PENDING.value:
        raise Conflict(f"Approval request already {approval.status}", code="APPROVAL_ALREADY_DECIDED")
    current_hash = state.current_baseline_hash(db, project)
    if current_hash != approval.baseline_hash or project.baseline_version != approval.baseline_version:
        raise Conflict("Baseline changed since approval was requested", code="APPROVAL_STALE")
    if expected_baseline_hash is not None and expected_baseline_hash != approval.baseline_hash:
        raise Conflict(
            "The baseline you reviewed differs from the request's baseline", code="REVIEWED_BASELINE_MISMATCH"
        )
    if len(reason.strip()) < 3:
        raise ValidationFailed("A decision reason is required", code="REASON_REQUIRED")
    if decision == "APPROVE":
        blockers = _blockers_excluding_approval(db, project)
        if blockers:
            raise Conflict("Baseline has unresolved blockers", code="BLOCKERS_PRESENT", extra={"blockers": blockers})
        approval.status = ApprovalStatus.APPROVED.value
    else:
        approval.status = ApprovalStatus.REJECTED.value
    approval.decision = decision
    approval.decided_by = actor.id
    approval.reviewed_at = utcnow()
    approval.decision_reason = reason
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="APPROVAL_DECIDED",
        resource_type="approval_request",
        resource_id=approval.id,
        before={"status": "PENDING"},
        after={
            "status": approval.status,
            "decision": decision,
            "reason": reason,
            "baseline_hash": approval.baseline_hash,
            "baseline_version": approval.baseline_version,
            "bom_revision_id": str(approval.bom_revision_id),
        },
    )
    outbox.enqueue(
        db,
        organization_id=project.organization_id,
        event_type="ApprovalDecided",
        aggregate_id=approval.id,
        payload={"project_id": str(project.id), "approval_id": str(approval.id), "decision": decision},
    )
    state.recompute_status(db, project, actor, f"APPROVAL_{decision}")
    return approval
