import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import Conflict, NotFound
from app.domain.bom import BomLine, content_hash
from app.models import BomItem, BomRevision, Project, User
from app.models.enums import BomRevisionStatus
from app.services import audit


def next_revision_number(db: Session, project_id: uuid.UUID) -> int:
    current = db.execute(
        select(func.max(BomRevision.revision_number)).where(BomRevision.project_id == project_id)
    ).scalar_one()
    return int(current or 0) + 1


def create_revision(
    db: Session,
    project: Project,
    lines: list[BomLine],
    actor: User,
    *,
    source_kind: str,
    source_ref: uuid.UUID | None,
    provenance: dict[str, dict[str, Any]] | None = None,
) -> BomRevision:
    """Insert a new immutable revision and make it current. Caller must hold the project lock."""
    number = next_revision_number(db, project.id)
    rev = BomRevision(
        organization_id=project.organization_id,
        project_id=project.id,
        revision_number=number,
        parent_revision_id=project.current_bom_revision_id,
        content_hash=content_hash(lines),
        status=BomRevisionStatus.PUBLISHED.value,
        source_kind=source_kind,
        source_ref=source_ref,
        created_by=actor.id,
    )
    db.add(rev)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise Conflict("Concurrent BOM revision publication", code="CONCURRENT_PUBLISH")
    for line in lines:
        db.add(
            BomItem(
                bom_revision_id=rev.id,
                line_key=line.line_key,
                part_number=line.part_number,
                description=line.description,
                quantity=line.quantity,
                unit=line.unit,
                part_revision=line.part_revision,
                attributes_json=line.attributes,
                provenance_json=(provenance or {}).get(line.line_key, {}),
            )
        )
    previous = project.current_bom_revision_id
    project.current_bom_revision_id = rev.id
    db.flush()
    audit.record(
        db,
        organization_id=project.organization_id,
        project_id=project.id,
        actor=actor,
        action="BOM_REVISION_PUBLISHED",
        resource_type="bom_revision",
        resource_id=rev.id,
        before={"current_bom_revision_id": str(previous) if previous else None},
        after={
            "revision": f"R{number}",
            "content_hash": rev.content_hash,
            "source_kind": source_kind,
            "source_ref": str(source_ref) if source_ref else None,
            "line_count": len(lines),
        },
    )
    return rev


def get_revision(db: Session, project: Project, number: int) -> BomRevision:
    rev = db.execute(
        select(BomRevision).where(BomRevision.project_id == project.id, BomRevision.revision_number == number)
    ).scalar_one_or_none()
    if rev is None:
        raise NotFound(f"Revision R{number} not found", code="REVISION_NOT_FOUND")
    return rev


def list_revisions(db: Session, project: Project) -> list[BomRevision]:
    return list(
        db.execute(
            select(BomRevision).where(BomRevision.project_id == project.id).order_by(BomRevision.revision_number)
        ).scalars()
    )


def items(db: Session, revision_id: uuid.UUID) -> list[BomItem]:
    return list(
        db.execute(select(BomItem).where(BomItem.bom_revision_id == revision_id).order_by(BomItem.line_key)).scalars()
    )
