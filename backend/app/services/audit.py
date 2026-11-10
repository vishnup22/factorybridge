import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditEvent, User
from app.observability.logging import correlation_id_var


def record(
    db: Session,
    *,
    organization_id: uuid.UUID,
    action: str,
    resource_type: str,
    resource_id: uuid.UUID | str | None,
    actor: User | None = None,
    actor_label: str | None = None,
    project_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append an audit event in the caller's transaction. Never commits; never updates."""
    ev = AuditEvent(
        organization_id=organization_id,
        project_id=project_id,
        actor_id=actor.id if actor else None,
        actor_label=actor_label or (f"{actor.email} ({actor.role})" if actor else "system"),
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id else None,
        before_json=before,
        after_json=after,
        correlation_id=correlation_id_var.get(),
    )
    db.add(ev)
    return ev
