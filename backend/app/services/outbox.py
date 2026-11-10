"""Transactional outbox. Domain writes call `enqueue` inside their transaction; a dispatcher later
publishes rows to the broker. Delivery is at-least-once; consumers must be idempotent."""

import logging
import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OutboxEvent
from app.models.base import utcnow
from app.observability.logging import correlation_id_var, log_event

logger = logging.getLogger(__name__)


def enqueue(
    db: Session, *, organization_id: uuid.UUID, event_type: str, aggregate_id: uuid.UUID, payload: dict[str, Any]
) -> OutboxEvent:
    ev = OutboxEvent(
        organization_id=organization_id,
        event_type=event_type,
        aggregate_id=aggregate_id,
        payload_json=payload,
        correlation_id=correlation_id_var.get(),
    )
    db.add(ev)
    return ev


def dispatch_pending(db: Session, publish: Callable[[OutboxEvent], None], limit: int = 50) -> int:
    """Publish unpublished events. Uses SKIP LOCKED so multiple dispatchers never double-send a row
    within one pass; a crash after publish but before commit causes a re-send (acceptable: consumers
    are idempotent)."""
    rows = (
        db.execute(
            select(OutboxEvent)
            .where(OutboxEvent.published_at.is_(None), OutboxEvent.attempts < 20)
            .order_by(OutboxEvent.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    sent = 0
    for ev in rows:
        ev.attempts += 1
        try:
            publish(ev)
        except Exception as exc:  # broker down etc. Leave unpublished for the next pass.
            ev.last_error = f"{type(exc).__name__}: {exc}"[:500]
            log_event(logger, "outbox_publish_failed", event_id=str(ev.id), error=ev.last_error)
            continue
        ev.published_at = utcnow()
        sent += 1
    db.commit()
    return sent
