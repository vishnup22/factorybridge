from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import Unauthorized

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def create_access_token(user_id: UUID, organization_id: UUID) -> tuple[str, int]:
    s = get_settings()
    now = datetime.now(UTC)
    exp = now + timedelta(minutes=s.jwt_ttl_minutes)
    # Role is deliberately NOT trusted from the token; it is re-read from the DB on every request.
    token = jwt.encode(
        {"sub": str(user_id), "org": str(organization_id), "iat": now, "exp": exp, "iss": s.jwt_issuer},
        s.jwt_secret,
        algorithm="HS256",
    )
    return token, s.jwt_ttl_minutes * 60


def decode_access_token(token: str) -> tuple[UUID, UUID]:
    s = get_settings()
    try:
        claims = jwt.decode(
            token, s.jwt_secret, algorithms=["HS256"], issuer=s.jwt_issuer, options={"require": ["exp", "sub"]}
        )
        return UUID(claims["sub"]), UUID(claims["org"])
    except (jwt.PyJWTError, KeyError, ValueError):
        raise Unauthorized("Invalid or expired token", code="INVALID_TOKEN")
