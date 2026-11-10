"""Engineer review of extraction candidates -> authoritative state (Invariant I-5 boundary).

Candidates only become BOM data here, via an explicit engineer decision per candidate:
- ACCEPT: allowed only for VERIFIED evidence with no conflicts or mandatory-field gaps.
- EDIT:   engineer-supplied value + mandatory correction note; provenance records the correction.
- REJECT: candidate discarded.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.domain.bom import BomLine, diff, validate_bom
from app.models import (
    Document,
    EngineeringChange,
    ExtractionRun,
    ManufacturingException,
    Project,
    RequirementCandidate,
    User,
)
from app.models.base import utcnow
from app.models.enums import DocumentStatus, ExceptionStatus, ExtractionStatus, ReviewState
from app.services import audit, bom_service, changes_service, exceptions_service, state

BLOCKING_ISSUES = {
    "MANDATORY_FIELD_MISSING",
    "EXTRACTION_CONFLICT",
    "EVIDENCE_NOT_FOUND",
    "VALUE_NOT_IN_EXCERPT",
    "EVIDENCE_MISSING",
    "EVIDENCE_PAGE_INVALID",
}


def latest_run(db: Session, doc: Document) -> ExtractionRun | None:
    return (
        db.execute(
            select(ExtractionRun).where(ExtractionRun.document_id == doc.id).order_by(ExtractionRun.created_at.desc())
        )
        .scalars()
        .first()
    )


def candidates(db: Session, run: ExtractionRun) -> list[RequirementCandidate]:
    return list(
        db.execute(
            select(RequirementCandidate)
            .where(RequirementCandidate.extraction_run_id == run.id)
            .order_by(RequirementCandidate.kind, RequirementCandidate.field_path)
        ).scalars()
    )


def accept(
    db: Session,
    project: Project,
    actor: User,
    doc: Document,
    *,
    decisions: list[dict[str, Any]],
    reason: str,
) -> dict[str, Any]:
    run = (
        db.execute(select(ExtractionRun).where(ExtractionRun.document_id == doc.id).with_for_update()).scalars().first()
    )
    if run is None:
        raise Conflict("Document has no extraction to review", code="EXTRACTION_NOT_READY")
    if run.status == ExtractionStatus.ACCEPTED.value:
        raise Conflict("Extraction already reviewed", code="EXTRACTION_ALREADY_ACCEPTED")
    if run.status != ExtractionStatus.REVIEW_REQUIRED.value:
        raise Conflict(f"Extraction is {run.status}", code="EXTRACTION_NOT_REVIEWABLE")
    cands = {c.id: c for c in candidates(db, run)}
    by_id: dict[uuid.UUID, dict[str, Any]] = {}
    for d in decisions:
        cid = uuid.UUID(str(d["candidate_id"]))
        if cid not in cands:
            raise ValidationFailed(f"Candidate {cid} does not belong to this extraction", code="UNKNOWN_CANDIDATE")
        by_id[cid] = d
    undecided = sorted(c.field_path for cid, c in cands.items() if cid not in by_id)
    if undecided:
        raise ValidationFailed(
            "Every candidate needs a decision", code="UNDECIDED_CANDIDATES", extra={"undecided": undecided}
        )
    problems: list[dict[str, Any]] = []
    raw_lines: list[dict[str, Any]] = []
    provenance: dict[str, dict[str, Any]] = {}
    now = utcnow()
    for cid, d in by_id.items():
        c = cands[cid]
        action = str(d.get("action", "")).upper()
        issue_codes = {i.get("code") for i in (c.issues_json or [])}
        if action == "ACCEPT":
            if (
                c.evidence_status != "VERIFIED"
                or c.review_state == ReviewState.CONFLICT.value
                or (issue_codes & BLOCKING_ISSUES)
            ):
                problems.append(
                    {
                        "field_path": c.field_path,
                        "code": "CANNOT_ACCEPT_AS_IS",
                        "message": f"evidence {c.evidence_status}; issues {sorted(issue_codes)}. "
                        "Edit with a note or reject.",
                    }
                )
                continue
            value = c.value_json
            c.review_state = ReviewState.ACCEPTED.value
        elif action == "EDIT":
            note = str(d.get("note") or "").strip()
            if not isinstance(d.get("value"), dict | str | int | float) or len(note) < 5:
                problems.append(
                    {
                        "field_path": c.field_path,
                        "code": "EDIT_REQUIRES_VALUE_AND_NOTE",
                        "message": "EDIT requires a corrected value and a correction note",
                    }
                )
                continue
            value = d["value"]
            c.corrected_value_json = value
            c.correction_note = note
            c.review_state = ReviewState.EDITED.value
        elif action == "REJECT":
            c.review_state = ReviewState.REJECTED.value
            value = None
        else:
            problems.append({"field_path": c.field_path, "code": "INVALID_ACTION", "message": action})
            continue
        c.reviewed_by = actor.id
        c.reviewed_at = now
        if value is not None and c.kind == "BOM_LINE":
            if not isinstance(value, dict):
                problems.append(
                    {"field_path": c.field_path, "code": "INVALID_VALUE", "message": "BOM value must be an object"}
                )
                continue
            raw_lines.append(value)
            provenance[str(value.get("line_key", "")).strip().upper()] = {
                "source": "extraction_review",
                "document_id": str(doc.id),
                "document": doc.filename,
                "extraction_run_id": str(run.id),
                "provider_mode": run.provider_mode,
                "candidate_id": str(c.id),
                "review_action": action,
                "page": c.source_page,
                "section": c.source_section,
                "excerpt": (c.source_excerpt or "")[:300],
                "correction_note": c.correction_note,
                "reviewed_by": actor.email,
            }
    if problems:
        raise ValidationFailed(
            "Some decisions are not allowed", code="INVALID_REVIEW_DECISIONS", extra={"problems": problems}
        )
    lines, issues = validate_bom(raw_lines) if raw_lines else ([], [])
    if issues:
        raise ValidationFailed(
            "Reviewed BOM is invalid", code="INVALID_BOM", extra={"issues": [i.as_dict() for i in issues]}
        )
    result: dict[str, Any] = {"bom_revision": None, "change_id": None, "change_code": None}
    if lines:
        if project.current_bom_revision_id is None:
            rev = bom_service.create_revision(
                db, project, lines, actor, source_kind="EXTRACTION_REVIEW", source_ref=run.id, provenance=provenance
            )
            run.resulting_bom_revision_id = rev.id
            state.invalidate_baseline(db, project, actor, f"BOM_REVISION_PUBLISHED:R{rev.revision_number}")
            result["bom_revision"] = rev.revision_number
        else:
            change = _propose_from_document(db, project, actor, lines, doc, reason)
            if change is not None:
                run.resulting_change_id = change.id
                result["change_id"] = str(change.id)
                result["change_code"] = change.code
    run.status = ExtractionStatus.ACCEPTED.value
    run.accepted_by = actor.id
    run.accepted_at = now
    doc.processing_status = DocumentStatus.ACCEPTED.value
    for exc in db.execute(
        select(ManufacturingException).where(
            ManufacturingException.extraction_run_id == run.id,
            ManufacturingException.status.in_(exceptions_service.OPEN_STATES),
        )
    ).scalars():
        exc.status = ExceptionStatus.RESOLVED.value
        exc.resolved_by = actor.id
        exc.resolved_at = now
        exc.resolution_note = f"Resolved by engineer extraction review: {reason}"
        audit.record(
            db,
            organization_id=project.organization_id,
            project_id=project.id,
            actor=actor,
            action="EXCEPTION_RESOLVED",
            resource_type="exception",
            resource_id=exc.id,
            after={"status": exc.status, "via": "extraction_review"},
        )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="EXTRACTION_REVIEWED",
        resource_type="extraction_run",
        resource_id=run.id,
        after={
            "decisions": {c.field_path: c.review_state for c in cands.values()},
            "reason": reason,
            **result,
        },
    )
    state.recompute_status(db, project, actor, "EXTRACTION_REVIEWED")
    return result


def _propose_from_document(
    db: Session, project: Project, actor: User, lines: list[BomLine], doc: Document, reason: str
) -> EngineeringChange | None:
    """Later documents never overwrite the BOM: they produce a PROPOSED engineering change that
    must be published (with impact analysis). Lines absent from the document are not removed."""
    current = state.current_revision(db, project)
    assert current is not None
    current_lines = state.load_lines(db, current.id)
    ops: list[dict[str, Any]] = []
    for d in diff(current_lines, lines):
        if d.change_type == "UPDATED":
            ops.append({"line_key": d.line_key, "operation": "UPDATE", "new": d.new})
        elif d.change_type == "ADDED":
            ops.append({"line_key": d.line_key, "operation": "ADD", "new": d.new})
    if not ops:
        return None
    return changes_service.propose_change(
        db,
        project,
        actor,
        base_revision_number=current.revision_number,
        reason=f"From reviewed document {doc.filename}: {reason}",
        operations=ops,
    )
