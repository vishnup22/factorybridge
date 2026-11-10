import secrets
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import func, select, text

from app.api.deps import DB, CurrentUser
from app.core.config import get_settings
from app.core.errors import Conflict, Forbidden, NotFound
from app.core.security import hash_password
from app.models import AuditEvent, OutboxEvent, ProcessingJob, SupplierOption, User
from app.models.enums import Role
from app.observability.metrics import metrics
from app.schemas.api import AuditOut, Envelope, SupplierOut, UserCreate, UserOut, UserUpdate
from app.services import audit
from app.services.access import require_role
from app.services.storage import get_storage

router = APIRouter()


# ---------------- health ----------------
@router.get("/health/live", tags=["health"])
def live() -> dict[str, str]:
    return {"status": "ok"}


def _health_token_ok(token: str | None) -> bool:
    expected = get_settings().health_token
    return expected is None or (token is not None and secrets.compare_digest(token, expected))


@router.get("/health/ready", tags=["health"])
def ready(db: DB, x_health_token: Annotated[str | None, Header()] = None) -> Any:
    if not _health_token_ok(x_health_token):
        raise Forbidden("Health token required", code="HEALTH_TOKEN_REQUIRED")
    checks: dict[str, str] = {}
    try:
        db.execute(text("SELECT 1"))
        rev = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
        checks["database"] = "ok"
        checks["migration"] = rev or "none"
    except Exception as e:
        checks["database"] = f"error:{type(e).__name__}"
    try:
        import redis

        redis.Redis.from_url(get_settings().redis_url, socket_timeout=1).ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error:{type(e).__name__}"
    checks["storage"] = "ok" if get_storage().healthy() else "error"
    try:
        backlog = db.execute(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
        ).scalar_one()
        checks["outbox_backlog"] = str(backlog)
    except Exception:
        checks["outbox_backlog"] = "unknown"
    ok = all(v == "ok" for k, v in checks.items() if k in ("database", "redis", "storage"))
    return JSONResponse({"status": "ok" if ok else "degraded", "checks": checks}, status_code=200 if ok else 503)


@router.get("/metrics", tags=["health"], response_class=PlainTextResponse)
def prometheus(db: DB, x_health_token: Annotated[str | None, Header()] = None) -> str:
    if not _health_token_ok(x_health_token):
        raise Forbidden("Health token required", code="HEALTH_TOKEN_REQUIRED")
    jobs = db.execute(select(ProcessingJob.status, func.count()).group_by(ProcessingJob.status)).all()
    backlog = db.execute(
        select(func.count()).select_from(OutboxEvent).where(OutboxEvent.published_at.is_(None))
    ).scalar_one()
    lines = metrics.render()
    lines += [f'factorybridge_jobs{{status="{s}"}} {n}' for s, n in jobs]
    lines.append(f"factorybridge_outbox_unpublished {backlog}")
    return "\n".join(lines) + "\n"


# ---------------- catalog ----------------
@router.get("/catalog/suppliers", tags=["catalog"], response_model=Envelope[list[SupplierOut]])
def suppliers(user: CurrentUser, db: DB, part_number: str | None = None) -> Envelope[list[SupplierOut]]:
    require_role(user, Role.ENGINEER, Role.APPROVER, Role.ADMIN)
    stmt = select(SupplierOption).where(SupplierOption.organization_id == user.organization_id)
    if part_number:
        stmt = stmt.where(SupplierOption.part_number == part_number)
    return Envelope(
        data=[SupplierOut.model_validate(s) for s in db.execute(stmt.order_by(SupplierOption.option_code)).scalars()]
    )


# ---------------- admin ----------------
@router.get("/admin/users", tags=["admin"], response_model=Envelope[list[UserOut]])
def list_users(user: CurrentUser, db: DB) -> Envelope[list[UserOut]]:
    require_role(user, Role.ADMIN)
    rows = db.execute(select(User).where(User.organization_id == user.organization_id).order_by(User.email)).scalars()
    return Envelope(data=[UserOut.model_validate(u) for u in rows])


@router.post("/admin/users", tags=["admin"], status_code=201, response_model=Envelope[UserOut])
def create_user(body: UserCreate, user: CurrentUser, db: DB) -> Envelope[UserOut]:
    require_role(user, Role.ADMIN)
    email = body.email.lower()
    if db.execute(select(User).where(User.organization_id == user.organization_id, User.email == email)).first():
        raise Conflict("User already exists", code="USER_EXISTS")
    u = User(
        organization_id=user.organization_id,
        email=email,
        full_name=body.full_name,
        role=body.role,
        password_hash=hash_password(body.password),
        is_active=True,
    )
    db.add(u)
    db.flush()
    audit.record(
        db,
        organization_id=user.organization_id,
        actor=user,
        action="USER_CREATED",
        resource_type="user",
        resource_id=u.id,
        after={"email": email, "role": body.role},
    )
    db.commit()
    return Envelope(data=UserOut.model_validate(u))


@router.patch("/admin/users/{user_id}", tags=["admin"], response_model=Envelope[UserOut])
def update_user(user_id: uuid.UUID, body: UserUpdate, user: CurrentUser, db: DB) -> Envelope[UserOut]:
    require_role(user, Role.ADMIN)
    u = db.get(User, user_id)
    if u is None or u.organization_id != user.organization_id:
        raise NotFound("User not found", code="USER_NOT_FOUND")
    if u.id == user.id:
        raise Forbidden("Admins cannot change their own role or status", code="SELF_MODIFICATION")
    before = {"role": u.role, "is_active": u.is_active}
    if body.role is not None:
        u.role = body.role
    if body.is_active is not None:
        u.is_active = body.is_active
    audit.record(
        db,
        organization_id=user.organization_id,
        actor=user,
        action="USER_UPDATED",
        resource_type="user",
        resource_id=u.id,
        before=before,
        after={"role": u.role, "is_active": u.is_active},
    )
    db.commit()
    return Envelope(data=UserOut.model_validate(u))


@router.get("/admin/audit", tags=["admin"], response_model=Envelope[list[AuditOut]])
def org_audit(user: CurrentUser, db: DB, limit: int = 100) -> Envelope[list[AuditOut]]:
    require_role(user, Role.ADMIN)
    rows = db.execute(
        select(AuditEvent)
        .where(AuditEvent.organization_id == user.organization_id)
        .order_by(AuditEvent.seq.desc())
        .limit(min(limit, 100))
    ).scalars()
    return Envelope(data=[AuditOut.model_validate(r) for r in rows])
