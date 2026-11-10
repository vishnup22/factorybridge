"""Organization-scoped lookups. Every fetch joins on organization_id from the authenticated user;
cross-organization IDs therefore return 404 without revealing existence."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import Forbidden, NotFound
from app.models import Document, Project, User
from app.models.enums import Role


def require_role(user: User, *roles: Role) -> None:
    if user.role not in {r.value for r in roles}:
        raise Forbidden(
            f"Role '{user.role}' may not perform this action", code="ROLE_FORBIDDEN", extra={"required": roles}
        )


def get_project(db: Session, user: User, project_id: uuid.UUID, *, lock: bool = False) -> Project:
    stmt = select(Project).where(Project.id == project_id, Project.organization_id == user.organization_id)
    if lock:
        stmt = stmt.with_for_update()
    project = db.execute(stmt).scalar_one_or_none()
    if project is None:
        raise NotFound("Project not found", code="PROJECT_NOT_FOUND")
    return project


def lock_project(db: Session, project: Project) -> Project:
    """Re-select the project row FOR UPDATE, serializing all baseline-affecting writes."""
    locked = db.execute(select(Project).where(Project.id == project.id).with_for_update()).scalar_one()
    db.refresh(locked)
    return locked


def get_document(db: Session, user: User, document_id: uuid.UUID) -> Document:
    doc = db.execute(
        select(Document).where(Document.id == document_id, Document.organization_id == user.organization_id)
    ).scalar_one_or_none()
    if doc is None:
        raise NotFound("Document not found", code="DOCUMENT_NOT_FOUND")
    return doc
