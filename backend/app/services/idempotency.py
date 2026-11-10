"""Idempotency-Key handling for externally retryable POSTs.

Same key + same request fingerprint -> stored response replayed.
Same key + different fingerprint    -> 409.
Requests with the same key are serialized with a transaction-scoped advisory lock.
Only successful (2xx) responses are stored; failures may be retried.
"""

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import Conflict, ValidationFailed
from app.models import IdempotencyRecord, User
from app.models.base import utcnow

RETENTION = timedelta(days=1)


def fingerprint(method: str, path: str, body: Any, user: User) -> str:
    blob = json.dumps({"m": method, "p": path, "b": body, "u": str(user.id)}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def run_idempotent(
    db: Session,
    *,
    user: User,
    key: str | None,
    request_hash: str,
    operation: Callable[[], tuple[int, Any]],
) -> tuple[int, Any, bool]:
    """Returns (status_code, body, replayed). `operation` must not commit."""
    if key is None:
        status, body = operation()
        db.commit()
        return status, body, False
    if not (1 <= len(key) <= 200):
        raise ValidationFailed("Idempotency-Key must be 1..200 characters", code="IDEMPOTENCY_KEY_INVALID")
    lock_id = f"{user.organization_id}:{key}"
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_id, 0))))
    existing = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.organization_id == user.organization_id, IdempotencyRecord.request_key == key
        )
    ).scalar_one_or_none()
    if existing is not None and existing.expires_at > utcnow():
        if existing.request_hash != request_hash:
            db.rollback()
            raise Conflict("Idempotency-Key was already used with a different request", code="IDEMPOTENCY_KEY_REUSED")
        status_code, response = existing.status_code, existing.response_json
        db.rollback()
        return status_code, response, True
    if existing is not None:
        db.delete(existing)
        db.flush()
    status, body = operation()
    db.add(
        IdempotencyRecord(
            organization_id=user.organization_id,
            user_id=user.id,
            request_key=key,
            request_hash=request_hash,
            status_code=status,
            response_json=body,
            expires_at=utcnow() + RETENTION,
        )
    )
    db.commit()
    return status, body, False
