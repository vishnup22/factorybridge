import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Header, Request, Response, UploadFile
from fastapi.responses import Response as RawResponse
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, limiter
from app.core.config import get_settings
from app.core.errors import NotFound, PayloadTooLarge
from app.models import ProcessingJob
from app.models.enums import Role
from app.schemas.api import (
    AcceptRequest,
    AcceptResponse,
    CandidateOut,
    DocumentDetail,
    DocumentOut,
    Envelope,
    ExtractionOut,
    ExtractionRunOut,
    JobOut,
)
from app.services import documents_service
from app.services.access import get_document, get_project, require_role
from app.services.extraction import review
from app.services.idempotency import fingerprint, run_idempotent
from app.services.storage import content_disposition, get_storage

router = APIRouter(tags=["documents"])


def _detail(db: DB, doc: Any, duplicate: bool = False) -> DocumentDetail:
    job = db.execute(
        select(ProcessingJob).where(ProcessingJob.dedup_key == documents_service.job_dedup_key(doc.id))
    ).scalar_one_or_none()
    return DocumentDetail(
        **DocumentOut.model_validate(doc).model_dump(),
        job=JobOut.model_validate(job) if job else None,
        download_url=f"/api/v1/documents/{doc.id}/download",
        duplicate=duplicate,
    )


@router.post("/projects/{project_id}/documents", status_code=202, response_model=Envelope[DocumentDetail])
async def upload_document(
    project_id: uuid.UUID, user: CurrentUser, db: DB, response: Response, file: UploadFile = File(...)
) -> Envelope[DocumentDetail]:
    require_role(user, Role.CUSTOMER, Role.ENGINEER)
    s = get_settings()
    limiter.hit(f"upload:{user.id}", s.upload_rate_limit_per_minute)
    project = get_project(db, user, project_id, lock=True)
    data = await file.read(s.max_upload_bytes + 1)
    if len(data) > s.max_upload_bytes:
        raise PayloadTooLarge(f"File exceeds {s.max_upload_bytes} bytes", code="FILE_TOO_LARGE")
    doc, _job, duplicate = documents_service.upload(db, project, user, filename=file.filename or "upload", data=data)
    db.commit()
    if duplicate:
        response.status_code = 200
    return Envelope(data=_detail(db, doc, duplicate))


@router.get("/documents/{document_id}", response_model=Envelope[DocumentDetail])
def get_document_detail(document_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[DocumentDetail]:
    return Envelope(data=_detail(db, get_document(db, user, document_id)))


@router.get("/documents/{document_id}/download")
def download(document_id: uuid.UUID, user: CurrentUser, db: DB) -> Any:
    doc = get_document(db, user, document_id)
    storage = get_storage()
    url = storage.signed_url(doc.storage_key, doc.filename)
    if url:
        return {"data": {"signed_url": url, "expires_in": 300}}
    return RawResponse(
        content=storage.get(doc.storage_key),
        media_type=doc.media_type,
        headers={
            "Content-Disposition": content_disposition(doc.filename),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/documents/{document_id}/extraction", response_model=Envelope[ExtractionOut])
def get_extraction(document_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[ExtractionOut]:
    require_role(user, Role.ENGINEER, Role.ADMIN)
    doc = get_document(db, user, document_id)
    run = review.latest_run(db, doc)
    cands = review.candidates(db, run) if run else []
    return Envelope(
        data=ExtractionOut(
            document=DocumentOut.model_validate(doc),
            run=ExtractionRunOut.model_validate(run) if run else None,
            candidates=[CandidateOut.model_validate(c) for c in cands],
        )
    )


@router.post("/documents/{document_id}/extraction/accept", response_model=Envelope[AcceptResponse])
def accept_extraction(
    document_id: uuid.UUID,
    body: AcceptRequest,
    request: Request,
    response: Response,
    user: CurrentUser,
    db: DB,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Any:
    require_role(user, Role.ENGINEER)

    def op() -> tuple[int, Any]:
        doc = get_document(db, user, document_id)
        project = get_project(db, user, doc.project_id, lock=True)
        result = review.accept(
            db, project, user, doc, decisions=[d.model_dump(mode="json") for d in body.decisions], reason=body.reason
        )
        db.flush()
        return 200, Envelope(data=AcceptResponse(**result, project_status=project.status)).model_dump(mode="json")

    status, payload, replayed = run_idempotent(
        db,
        user=user,
        key=idempotency_key,
        request_hash=fingerprint("POST", request.url.path, body.model_dump(mode="json"), user),
        operation=op,
    )
    response.status_code = status
    if replayed:
        response.headers["Idempotent-Replayed"] = "true"
    return payload


@router.get("/jobs/{job_id}", response_model=Envelope[JobOut])
def get_job(job_id: uuid.UUID, user: CurrentUser, db: DB) -> Envelope[JobOut]:
    job = db.execute(
        select(ProcessingJob).where(ProcessingJob.id == job_id, ProcessingJob.organization_id == user.organization_id)
    ).scalar_one_or_none()
    if job is None:
        raise NotFound("Job not found", code="JOB_NOT_FOUND")
    return Envelope(data=JobOut.model_validate(job))
