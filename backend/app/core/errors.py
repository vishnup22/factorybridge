"""RFC 7807-style problem errors used by services and translated by the API layer."""

from typing import Any


class DomainError(Exception):
    status_code = 400
    code = "BAD_REQUEST"
    title = "Bad request"

    def __init__(self, detail: str, *, code: str | None = None, extra: dict[str, Any] | None = None):
        super().__init__(detail)
        self.detail = detail
        if code:
            self.code = code
        self.extra = extra or {}


class ValidationFailed(DomainError):
    status_code = 422
    code = "VALIDATION_FAILED"
    title = "Validation failed"


class NotFound(DomainError):
    status_code = 404
    code = "NOT_FOUND"
    title = "Not found"


class Forbidden(DomainError):
    status_code = 403
    code = "FORBIDDEN"
    title = "Forbidden"


class Unauthorized(DomainError):
    status_code = 401
    code = "UNAUTHORIZED"
    title = "Unauthorized"


class Conflict(DomainError):
    status_code = 409
    code = "CONFLICT"
    title = "Conflict"


class PayloadTooLarge(DomainError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"
    title = "Payload too large"


class RateLimited(DomainError):
    status_code = 429
    code = "RATE_LIMITED"
    title = "Too many requests"
