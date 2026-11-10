import hashlib
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import PayloadTooLarge, ValidationFailed
from app.models import Document, ProcessingJob, Project, User
from app.models.enums import DocumentStatus, JobStatus
from app.services import audit, outbox, state
from app.services.extraction.parsers import ParseError, sniff
from app.services.storage import get_storage

EXTRACTION_VERSION = "v1"
MEDIA_TYPES = {"pdf": "application/pdf", "csv": "text/csv"}
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def safe_display_name(filename: str) -> str:
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    base = _CTRL.sub("", base).strip() or "upload"
    return base[:255]


def job_dedup_key(document_id: uuid.UUID) -> str:
    return f"extract:{document_id}:{EXTRACTION_VERSION}"


def upload(
    db: Session, project: Project, actor: User, *, filename: str, data: bytes
) -> tuple[Document, ProcessingJob | None, bool]:
    """Validate, store privately, and record Document + job + outbox event in one transaction.
    Returns (document, job, duplicate)."""
    s = get_settings()
    if len(data) > s.max_upload_bytes:
        raise PayloadTooLarge(f"File exceeds {s.max_upload_bytes} bytes", code="FILE_TOO_LARGE")
    if not data:
        raise ValidationFailed("File is empty", code="FILE_EMPTY")
    name = safe_display_name(filename)
    try:
        kind = sniff(name, data)
    except ParseError as e:
        raise ValidationFailed(e.message, code=e.code)
    digest = hashlib.sha256(data).hexdigest()
    existing = db.execute(
        select(Document).where(Document.project_id == project.id, Document.sha256 == digest)
    ).scalar_one_or_none()
    if existing is not None:
        job = db.execute(
            select(ProcessingJob).where(ProcessingJob.dedup_key == job_dedup_key(existing.id))
        ).scalar_one_or_none()
        return existing, job, True
    doc_id = uuid.uuid4()
    key = f"{project.organization_id}/{project.id}/{doc_id}"
    get_storage().put(key, data, MEDIA_TYPES[kind])
    doc = Document(
        id=doc_id,
        organization_id=project.organization_id,
        project_id=project.id,
        filename=name,
        media_type=MEDIA_TYPES[kind],
        sha256=digest,
        size_bytes=len(data),
        storage_key=key,
        processing_status=DocumentStatus.PENDING.value,
        uploaded_by=actor.id,
    )
    db.add(doc)
    db.flush()
    job = ProcessingJob(
        organization_id=project.organization_id,
        project_id=project.id,
        kind="DOCUMENT_EXTRACTION",
        resource_id=doc.id,
        dedup_key=job_dedup_key(doc.id),
        status=JobStatus.QUEUED.value,
    )
    db.add(job)
    db.flush()
    outbox.enqueue(
        db,
        organization_id=project.organization_id,
        event_type="DocumentUploaded",
        aggregate_id=doc.id,
        payload={"document_id": str(doc.id), "job_id": str(job.id)},
    )
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="DOCUMENT_UPLOADED",
        resource_type="document",
        resource_id=doc.id,
        after={"filename": name, "media_type": doc.media_type, "sha256": digest, "size_bytes": len(data)},
    )
    state.recompute_status(db, project, actor, "DOCUMENT_UPLOADED")
    return doc, job, False
