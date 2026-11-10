"""Worker-side document processing. Safe under at-least-once delivery and worker death:

1. claim   (short tx): lock job row; skip if finished or freshly leased; take a new lease.
2. work    (no tx):    read private blob, verify checksum, parse, extract, verify evidence.
3. commit  (one tx):   lock project + job; abort if lease was taken over; write run, candidates,
                       exceptions, statuses, audit atomically.

A crash before step 3 commits leaves nothing behind except a RUNNING job whose lease expires; the
recovery sweep re-enqueues it. A replay after step 3 sees SUCCEEDED and is a no-op.
"""

from __future__ import annotations

import hashlib
import logging
import random
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import session_factory
from app.domain.bom import coerce_line
from app.models import Document, ExtractionRun, ProcessingJob, Project, RequirementCandidate
from app.models.base import utcnow
from app.models.enums import DocumentStatus, ExtractionStatus, JobStatus
from app.observability.logging import log_event
from app.services import audit, exceptions_service, state
from app.services.documents_service import EXTRACTION_VERSION
from app.services.extraction.evidence import verify_bom_candidate, verify_requirement
from app.services.extraction.parsers import ParseError, parse_csv, parse_pdf
from app.services.extraction.providers import PROMPT_VERSION, ExtractionProvider, ProviderError, get_provider
from app.services.extraction.schema import SCHEMA_VERSION
from app.services.storage import Storage, get_storage

logger = logging.getLogger(__name__)

FaultHook = Callable[[str], None]


class SimulatedCrash(BaseException):  # noqa: N818 - BaseException so generic handlers don't swallow it
    """Raised by test fault hooks to emulate abrupt worker death."""


@dataclass
class Outcome:
    status: str  # DONE | SKIPPED | RETRY | FAILED
    retry_in_seconds: float | None = None
    detail: str = ""


@dataclass
class CandidateDraft:
    kind: str
    field_path: str
    value: Any
    unit: str | None
    page: int | None
    section: str | None
    excerpt: str | None
    evidence_status: str
    confidence: str
    issues: list[dict[str, Any]] = field(default_factory=list)
    review_state: str = "PENDING"


@dataclass
class WorkResult:
    provider_mode: str
    model: str
    page_count: int | None
    drafts: list[CandidateDraft]
    summary: dict[str, Any]
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    cost: Decimal | None = None
    cost_assumptions: dict[str, Any] | None = None


def run_dedup_key(document_id: uuid.UUID) -> str:
    return f"run:{document_id}:{EXTRACTION_VERSION}"


def _claim(db: Session, job_id: uuid.UUID) -> tuple[uuid.UUID, Document] | Outcome:
    s = get_settings()
    job = db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()).scalar_one_or_none()
    if job is None:
        return Outcome("SKIPPED", detail="job not found")
    now = utcnow()
    if job.status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
        return Outcome("SKIPPED", detail=f"already {job.status}")
    if (
        job.status == JobStatus.RUNNING
        and job.started_at
        and job.started_at > now - timedelta(seconds=s.job_stale_after_seconds)
    ):
        return Outcome("SKIPPED", detail="leased by another worker")
    if job.status == JobStatus.RETRY_SCHEDULED and job.next_retry_at and job.next_retry_at > now + timedelta(seconds=2):
        return Outcome("SKIPPED", detail="retry not due")
    doc = db.get(Document, job.resource_id)
    if doc is None:
        return Outcome("SKIPPED", detail="document missing")
    lease = uuid.uuid4()
    job.status = JobStatus.RUNNING.value
    job.attempt += 1
    job.lease_token = lease
    job.started_at = now
    job.next_retry_at = None
    doc.processing_status = DocumentStatus.PROCESSING.value
    db.commit()
    return lease, doc


def _csv_drafts(data: bytes) -> tuple[list[CandidateDraft], dict[str, Any]]:
    rows = parse_csv(data)
    drafts: list[CandidateDraft] = []
    keys: dict[str, list[int]] = {}
    for row in rows:
        line, issues = coerce_line(row.record)
        key = (
            line.line_key if line else str(row.record.get("line_key") or "").strip().upper()
        ) or f"ROW{row.row_number}"
        keys.setdefault(key, []).append(len(drafts))
        drafts.append(
            CandidateDraft(
                kind="BOM_LINE",
                field_path=f"bom.{key}",
                value=line.canonical() if line else row.record,
                unit=line.unit if line else row.record.get("unit"),
                page=None,
                section=f"row {row.row_number}",
                excerpt=row.raw_line,
                evidence_status="VERIFIED",
                confidence="HIGH" if not issues else "LOW",
                issues=[i.as_dict() for i in issues],
            )
        )
    conflicts = _mark_conflicts(drafts, keys)
    return drafts, {"row_count": len(rows), "conflicts": conflicts, "missing_fields": [], "ambiguities": []}


def _mark_conflicts(drafts: list[CandidateDraft], keys: dict[str, list[int]]) -> list[str]:
    conflicts: list[str] = []
    for key, idxs in sorted(keys.items()):
        if len(idxs) < 2:
            continue
        conflicts.append(key)
        for n, i in enumerate(idxs):
            d = drafts[i]
            d.review_state = "CONFLICT"
            d.issues.append(
                {"code": "EXTRACTION_CONFLICT", "message": f"{key} appears {len(idxs)} times with differing values"}
            )
            if n:
                d.field_path = f"{d.field_path}#{n + 1}"
    return conflicts


def _pdf_drafts(pages: list[str], provider: ExtractionProvider, request_id: str) -> WorkResult:
    result = provider.extract(pages, request_id)
    out = result.output
    drafts: list[CandidateDraft] = []
    keys: dict[str, list[int]] = {}
    seen_values: dict[str, dict[str, Any]] = {}
    for c in out.bom_candidates:
        ev_status, ev_issues = verify_bom_candidate(c, pages)
        raw = {
            "line_key": c.line_key,
            "part_number": c.part_number,
            "description": c.description,
            "quantity": c.quantity,
            "unit": c.unit,
            "part_revision": c.part_revision,
            "attributes": c.attributes,
        }
        line, line_issues = coerce_line(raw)
        issues = list(ev_issues)
        for i in line_issues:
            d = i.as_dict()
            if d["code"] in ("MISSING_PART_NUMBER", "MISSING_LINE_KEY", "INVALID_QUANTITY", "MISSING_PART_REVISION"):
                d["code"] = "MANDATORY_FIELD_MISSING"
            issues.append(d)
        key = (c.line_key or "").strip().upper() or f"UNKEYED{len(drafts) + 1}"
        value = line.canonical() if line else raw
        if key in seen_values and seen_values[key] == value:
            continue  # identical repeat citation; keep the first
        seen_values.setdefault(key, value)
        keys.setdefault(key, []).append(len(drafts))
        drafts.append(
            CandidateDraft(
                kind="BOM_LINE",
                field_path=f"bom.{key}",
                value=value,
                unit=c.unit,
                page=c.evidence.page_number,
                section=c.evidence.section,
                excerpt=c.evidence.exact_excerpt or None,
                evidence_status=ev_status,
                confidence=c.confidence if not issues else ("LOW" if c.confidence == "HIGH" else c.confidence),
                issues=issues,
            )
        )
    conflicts = _mark_conflicts(drafts, keys)
    req_keys: dict[str, int] = {}
    for r in out.requirements:
        ev_status, issues = verify_requirement(r, pages)
        n = req_keys.get(r.key, 0) + 1
        req_keys[r.key] = n
        drafts.append(
            CandidateDraft(
                kind="REQUIREMENT",
                field_path=f"requirements.{r.key}" + (f"#{n}" if n > 1 else ""),
                value=r.value,
                unit=r.unit,
                page=r.evidence.page_number,
                section=r.evidence.section,
                excerpt=r.evidence.exact_excerpt or None,
                evidence_status=ev_status,
                confidence=r.confidence,
                issues=issues,
            )
        )
    if not drafts:
        raise ProviderError("NO_CANDIDATES", "Extraction produced no candidates", retryable=False)
    if all(d.evidence_status != "VERIFIED" for d in drafts):
        raise ProviderError(
            "NO_EVIDENCE", "No candidate could be verified against the document text", retryable=provider.mode == "LIVE"
        )
    md = result.metadata
    s = get_settings()
    cost = None
    assumptions = None
    if md.input_tokens is not None and md.output_tokens is not None:
        cost = (
            Decimal(md.input_tokens) * Decimal(str(s.ai_input_cost_per_mtok_usd))
            + Decimal(md.output_tokens) * Decimal(str(s.ai_output_cost_per_mtok_usd))
        ) / Decimal(1_000_000)
        assumptions = {
            "input_usd_per_mtok": s.ai_input_cost_per_mtok_usd,
            "output_usd_per_mtok": s.ai_output_cost_per_mtok_usd,
            "note": "Configured estimate, not a billed amount",
        }
    return WorkResult(
        provider_mode=md.provider_mode,
        model=md.model,
        page_count=len(pages),
        drafts=drafts,
        summary={
            "project_name": out.project_name,
            "product_sku": out.product_sku,
            "target_quantity": out.target_quantity,
            "missing_fields": out.missing_fields,
            "ambiguities": out.ambiguities,
            "conflicts": conflicts,
        },
        input_tokens=md.input_tokens,
        output_tokens=md.output_tokens,
        latency_ms=md.latency_ms,
        cost=cost,
        cost_assumptions=assumptions,
    )


def _check_budget(db: Session, organization_id: uuid.UUID) -> None:
    s = get_settings()
    since = utcnow() - timedelta(days=1)
    spent = db.execute(
        select(func.coalesce(func.sum(ExtractionRun.cost_usd_estimate), 0)).where(
            ExtractionRun.organization_id == organization_id, ExtractionRun.created_at >= since
        )
    ).scalar_one()
    if Decimal(str(spent or 0)) >= Decimal(str(s.ai_daily_budget_usd)):
        raise ProviderError("AI_BUDGET_EXCEEDED", "Daily AI budget cap reached", retryable=False)


def _do_work(doc: Document, storage: Storage, provider: ExtractionProvider | None, job_id: uuid.UUID) -> WorkResult:
    data = storage.get(doc.storage_key)
    if hashlib.sha256(data).hexdigest() != doc.sha256:
        raise ParseError("CHECKSUM_MISMATCH", "Stored file does not match its recorded checksum")
    if doc.media_type == "text/csv":
        drafts, summary = _csv_drafts(data)
        return WorkResult("CSV_DETERMINISTIC", "csv-parser-v1", None, drafts, summary)
    pages = parse_pdf(data)
    prov = provider or get_provider()
    if prov.mode == "LIVE":
        with session_factory()() as db:
            _check_budget(db, doc.organization_id)
    return _pdf_drafts(pages, prov, str(job_id))


def _commit_success(
    db: Session, job_id: uuid.UUID, lease: uuid.UUID, doc_id: uuid.UUID, work: WorkResult, fault: FaultHook | None
) -> Outcome:
    doc = db.get(Document, doc_id)
    assert doc is not None
    project = db.execute(select(Project).where(Project.id == doc.project_id).with_for_update()).scalar_one()
    job = db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()).scalar_one()
    if job.lease_token != lease or job.status != JobStatus.RUNNING:
        db.rollback()
        return Outcome("SKIPPED", detail="lease lost")
    if db.execute(select(ExtractionRun.id).where(ExtractionRun.dedup_key == run_dedup_key(doc.id))).first():
        job.status = JobStatus.SUCCEEDED.value
        job.finished_at = utcnow()
        db.commit()
        return Outcome("SKIPPED", detail="run already recorded")
    run = ExtractionRun(
        organization_id=doc.organization_id,
        project_id=doc.project_id,
        document_id=doc.id,
        dedup_key=run_dedup_key(doc.id),
        provider_mode=work.provider_mode,
        model=work.model,
        prompt_version=PROMPT_VERSION if work.provider_mode != "CSV_DETERMINISTIC" else "n/a",
        schema_version=SCHEMA_VERSION,
        status=ExtractionStatus.REVIEW_REQUIRED.value,
        input_tokens=work.input_tokens,
        output_tokens=work.output_tokens,
        latency_ms=work.latency_ms,
        cost_usd_estimate=work.cost,
        cost_assumptions=work.cost_assumptions,
        summary_json=work.summary,
    )
    db.add(run)
    db.flush()
    for d in work.drafts:
        db.add(
            RequirementCandidate(
                extraction_run_id=run.id,
                kind=d.kind,
                field_path=d.field_path,
                value_json=d.value,
                unit=d.unit,
                source_page=d.page,
                source_section=d.section,
                source_excerpt=d.excerpt,
                evidence_status=d.evidence_status,
                confidence_label=d.confidence,
                review_state=d.review_state,
                issues_json=d.issues,
            )
        )
        codes = {i["code"] for i in d.issues}
        for reason in sorted(codes & {"EXTRACTION_CONFLICT", "MANDATORY_FIELD_MISSING"}):
            line_key = d.field_path.split(".", 1)[1].split("#", 1)[0]
            exceptions_service.upsert_exception(
                db,
                project,
                dedup_key=f"extraction:{run.id}:{line_key}:{reason}",
                reason_code=reason,
                severity="HIGH",
                description=f"{doc.filename}: {line_key} — {reason.replace('_', ' ').lower()}",
                required_action="Resolve during extraction review (edit with a correction note or reject).",
                evidence={"document_id": str(doc.id), "field_path": d.field_path, "page": d.page, "issues": d.issues},
                actor=None,
                extraction_run_id=run.id,
            )
    doc.processing_status = DocumentStatus.REVIEW_REQUIRED.value
    doc.page_count = work.page_count
    doc.error_code = None
    doc.error_detail = None
    job.status = JobStatus.SUCCEEDED.value
    job.finished_at = utcnow()
    job.error_code = None
    audit.record(
        db,
        organization_id=doc.organization_id,
        project_id=doc.project_id,
        actor_label=f"worker ({work.provider_mode})",
        action="EXTRACTION_COMPLETED",
        resource_type="extraction_run",
        resource_id=run.id,
        after={
            "document_id": str(doc.id),
            "provider_mode": work.provider_mode,
            "model": work.model,
            "candidates": len(work.drafts),
            "unverified": sum(1 for d in work.drafts if d.evidence_status != "VERIFIED"),
            "conflicts": work.summary.get("conflicts", []),
        },
    )
    state.recompute_status(db, project, None, "EXTRACTION_COMPLETED")
    db.flush()
    if fault:
        fault("before_commit")
    db.commit()
    return Outcome("DONE")


def _commit_failure(
    db: Session, job_id: uuid.UUID, lease: uuid.UUID, doc_id: uuid.UUID, code: str, message: str, retryable: bool
) -> Outcome:
    s = get_settings()
    doc = db.get(Document, doc_id)
    assert doc is not None
    project = db.execute(select(Project).where(Project.id == doc.project_id).with_for_update()).scalar_one()
    job = db.execute(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()).scalar_one()
    if job.lease_token != lease:
        db.rollback()
        return Outcome("SKIPPED", detail="lease lost")
    job.error_code = code
    job.error_detail = message[:1000]
    if retryable and job.attempt < s.max_job_attempts:
        delay = min(60.0, 2.0**job.attempt) + random.uniform(0, 1.0)  # noqa: S311 - jitter, not crypto
        job.status = JobStatus.RETRY_SCHEDULED.value
        job.next_retry_at = utcnow() + timedelta(seconds=delay)
        doc.error_code = code
        doc.error_detail = f"Attempt {job.attempt} failed; retrying"
        db.commit()
        return Outcome("RETRY", retry_in_seconds=delay, detail=code)
    job.status = JobStatus.FAILED.value
    job.finished_at = utcnow()
    doc.processing_status = DocumentStatus.FAILED.value
    doc.error_code = code
    doc.error_detail = message[:1000]
    if not db.execute(select(ExtractionRun.id).where(ExtractionRun.dedup_key == run_dedup_key(doc.id))).first():
        db.add(
            ExtractionRun(
                organization_id=doc.organization_id,
                project_id=doc.project_id,
                document_id=doc.id,
                dedup_key=run_dedup_key(doc.id),
                provider_mode="CSV_DETERMINISTIC"
                if doc.media_type == "text/csv"
                else ("LIVE" if s.ai_provider == "anthropic" else "FIXTURE"),
                model="n/a",
                prompt_version=PROMPT_VERSION,
                schema_version=SCHEMA_VERSION,
                status=ExtractionStatus.FAILED.value,
                error_code=code,
                error_detail=message[:1000],
                summary_json={},
            )
        )
    audit.record(
        db,
        organization_id=doc.organization_id,
        project_id=doc.project_id,
        actor_label="worker",
        action="EXTRACTION_FAILED",
        resource_type="document",
        resource_id=doc.id,
        after={"error_code": code, "attempts": job.attempt},
    )
    state.recompute_status(db, project, None, "EXTRACTION_FAILED")
    db.commit()
    return Outcome("FAILED", detail=code)


def process_job(
    job_id: uuid.UUID,
    *,
    provider: ExtractionProvider | None = None,
    storage: Storage | None = None,
    fault: FaultHook | None = None,
) -> Outcome:
    Session_ = session_factory()
    with Session_() as db:
        claimed = _claim(db, job_id)
    if isinstance(claimed, Outcome):
        return claimed
    lease, doc = claimed
    try:
        work = _do_work(doc, storage or get_storage(), provider, job_id)
        if fault:
            fault("after_work")
    except ParseError as e:
        with Session_() as db:
            return _commit_failure(db, job_id, lease, doc.id, e.code, e.message, retryable=False)
    except ProviderError as e:
        with Session_() as db:
            return _commit_failure(db, job_id, lease, doc.id, e.code, e.message, retryable=e.retryable)
    except Exception as e:
        log_event(logger, "extraction_unexpected_error", job_id=str(job_id), error=type(e).__name__)
        with Session_() as db:
            return _commit_failure(db, job_id, lease, doc.id, "INTERNAL_ERROR", type(e).__name__, retryable=True)
    with Session_() as db:
        return _commit_success(db, job_id, lease, doc.id, work, fault)


def recoverable_jobs(db: Session) -> list[uuid.UUID]:
    """Jobs that should be (re-)enqueued: lost QUEUED messages, expired RUNNING leases, overdue retries."""
    s = get_settings()
    now = utcnow()
    rows = db.execute(
        select(ProcessingJob.id).where(
            ((ProcessingJob.status == JobStatus.QUEUED) & (ProcessingJob.updated_at < now - timedelta(seconds=60)))
            | (
                (ProcessingJob.status == JobStatus.RUNNING)
                & (ProcessingJob.started_at < now - timedelta(seconds=s.job_stale_after_seconds))
            )
            | (
                (ProcessingJob.status == JobStatus.RETRY_SCHEDULED)
                & (ProcessingJob.next_retry_at < now - timedelta(seconds=30))
            )
        )
    ).scalars()
    return list(rows)
