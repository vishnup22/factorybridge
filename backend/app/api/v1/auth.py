from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import DB, CurrentUser, client_ip, limiter
from app.core.config import get_settings
from app.core.errors import Unauthorized, ValidationFailed
from app.core.security import create_access_token, hash_password, verify_password
from app.models import Organization, User
from app.schemas.api import LoginRequest, LoginResponse, MeResponse, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Verify against a real dummy hash when the user does not exist, so timing doesn't reveal accounts.
_DUMMY_HASH = hash_password("dummy-password-for-timing-only")


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, db: DB) -> LoginResponse:
    limit = get_settings().login_rate_limit_per_minute
    limiter.hit(f"login:{client_ip(request)}", limit)
    limiter.hit(f"login-account:{body.email.lower()}", limit)
    stmt = select(User).where(User.email == body.email.lower())
    if body.organization_slug:
        stmt = stmt.join(Organization, Organization.id == User.organization_id).where(
            Organization.slug == body.organization_slug
        )
    users = db.execute(stmt).scalars().all()
    if len(users) > 1:
        raise ValidationFailed("organization_slug is required for this account", code="ORG_REQUIRED")
    user = users[0] if users else None
    ok = verify_password(user.password_hash if user else _DUMMY_HASH, body.password)
    if user is None or not ok or not user.is_active:
        if user is not None:
            audit.record(
                db,
                organization_id=user.organization_id,
                actor=None,
                actor_label="anonymous",
                action="LOGIN_FAILED",
                resource_type="user",
                resource_id=user.id,
                after={"reason": "inactive" if ok else "bad_credentials"},
            )
            db.commit()
        raise Unauthorized("Invalid credentials", code="INVALID_CREDENTIALS")
    token, ttl = create_access_token(user.id, user.organization_id)
    audit.record(
        db,
        organization_id=user.organization_id,
        actor=user,
        action="LOGIN_SUCCEEDED",
        resource_type="user",
        resource_id=user.id,
    )
    db.commit()
    return LoginResponse(access_token=token, expires_in=ttl, user=UserOut.model_validate(user))


@router.get("/me", response_model=MeResponse)
def me(user: CurrentUser, db: DB) -> MeResponse:
    org = db.get(Organization, user.organization_id)
    s = get_settings()
    return MeResponse(
        user=UserOut.model_validate(user),
        organization={"id": str(org.id), "name": org.name, "slug": org.slug} if org else {},
        ai_mode="LIVE" if s.ai_provider == "anthropic" else "FIXTURE",
        environment=s.environment,
    )
