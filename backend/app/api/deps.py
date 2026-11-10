import time
from collections import defaultdict
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import RateLimited, Unauthorized
from app.core.security import decode_access_token
from app.models import User

DB = Annotated[Session, Depends(get_db)]


def current_user(db: DB, authorization: Annotated[str | None, Header()] = None) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token", code="MISSING_TOKEN")
    user_id, org_id = decode_access_token(authorization.split(" ", 1)[1].strip())
    user = db.get(User, user_id)
    # Role and active state are always read from the database, never from the token or request.
    if user is None or user.organization_id != org_id or not user.is_active:
        raise Unauthorized("User inactive or not found", code="USER_INACTIVE")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


class RateLimiter:
    """Fixed-window limiter backed by Redis, with an in-process fallback when Redis is unavailable."""

    def __init__(self) -> None:
        self._local: dict[str, list[float]] = defaultdict(list)
        self._redis: Any = None

    def _client(self) -> Any:
        if self._redis is None:
            import redis

            self._redis = redis.Redis.from_url(get_settings().redis_url, socket_timeout=0.5)
        return self._redis

    def hit(self, bucket: str, limit: int, window: int = 60) -> None:
        key = f"rl:{bucket}:{int(time.time() // window)}"
        try:
            client = self._client()
            count = client.incr(key)
            if count == 1:
                client.expire(key, window)
        except Exception:
            now = time.time()
            hits = [t for t in self._local[bucket] if t > now - window]
            hits.append(now)
            self._local[bucket] = hits
            count = len(hits)
        if int(count) > limit:
            raise RateLimited("Too many requests; slow down", code="RATE_LIMITED")


limiter = RateLimiter()


def client_ip(request: Request) -> str:
    # Only the entries appended by our own proxies are trustworthy; anything to their left is client-controlled.
    hops = get_settings().trusted_proxy_hops
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if hops > 0 and len(forwarded) >= hops:
        return forwarded[-hops]
    return request.client.host if request.client else "unknown"
