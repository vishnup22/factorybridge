import logging
import uuid

from app.core.db import session_factory
from app.models import OutboxEvent
from app.observability.logging import correlation_id_var, log_event
from app.services import outbox
from app.services.extraction import pipeline
from app.workers.celery_app import celery_app

logger = logging.getLogger("factorybridge.worker")


def publish_event(ev: OutboxEvent) -> None:
    """Map outbox events to tasks. Only DocumentUploaded has async work; other events are recorded
    (for traceability) and acknowledged."""
    if ev.event_type == "DocumentUploaded":
        process_document.apply_async(args=[ev.payload_json["job_id"]], headers={"correlation_id": ev.correlation_id})
    else:
        log_event(logger, "event_consumed", event_type=ev.event_type, aggregate_id=str(ev.aggregate_id))


@celery_app.task(name="factorybridge.dispatch_outbox")
def dispatch_outbox() -> int:
    with session_factory()() as db:
        return outbox.dispatch_pending(db, publish_event)


@celery_app.task(name="factorybridge.process_document", bind=True, max_retries=5)
def process_document(self, job_id: str) -> str:  # type: ignore[no-untyped-def]
    correlation_id_var.set((self.request.headers or {}).get("correlation_id"))
    outcome = pipeline.process_job(uuid.UUID(job_id))
    log_event(logger, "job_processed", job_id=job_id, status=outcome.status, detail=outcome.detail)
    if outcome.status == "RETRY" and outcome.retry_in_seconds is not None:
        raise self.retry(countdown=outcome.retry_in_seconds)
    return outcome.status


@celery_app.task(name="factorybridge.recover_jobs")
def recover_jobs() -> int:
    with session_factory()() as db:
        ids = pipeline.recoverable_jobs(db)
    for job_id in ids:
        process_document.delay(str(job_id))
    if ids:
        log_event(logger, "jobs_recovered", count=len(ids))
    return len(ids)
