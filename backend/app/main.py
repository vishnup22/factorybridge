import logging
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1 import auth, documents, misc, projects
from app.core.config import get_settings
from app.core.errors import DomainError
from app.observability.logging import configure_logging, correlation_id_var, log_event
from app.observability.metrics import metrics

logger = logging.getLogger("factorybridge.api")
_CID = re.compile(r"^[A-Za-z0-9\-]{8,64}$")


def _problem(status: int, title: str, detail: str, code: str, extra: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        media_type="application/problem+json",
        content={
            "type": "about:blank",
            "title": title,
            "status": status,
            "detail": detail,
            "code": code,
            "correlation_id": correlation_id_var.get(),
            "extra": extra or {},
        },
    )


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="FactoryBridge API",
        version="0.1.0",
        description="Manufacturing change intelligence. SYNTHETIC DATA / SIMULATION — NOT MANUFACTURING CERTIFICATION.",
        docs_url="/api/docs" if settings.environment != "production" else None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "Idempotent-Replayed"],
    )

    @app.middleware("http")
    async def correlation(request: Request, call_next):  # type: ignore[no-untyped-def]
        incoming = request.headers.get("x-correlation-id", "")
        cid = incoming if _CID.match(incoming) else uuid.uuid4().hex
        token = correlation_id_var.set(cid)
        started = time.perf_counter()
        try:
            response = await call_next(request)
            elapsed = (time.perf_counter() - started) * 1000
            path = getattr(request.scope.get("route"), "path", "unmatched")
            metrics.observe(path, response.status_code, elapsed)
            response.headers["X-Correlation-ID"] = cid
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            if settings.environment == "production":
                response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
            log_event(
                logger,
                "request",
                method=request.method,
                path=path,
                status=response.status_code,
                latency_ms=round(elapsed, 1),
            )
            return response
        finally:
            correlation_id_var.reset(token)

    @app.exception_handler(DomainError)
    async def domain_error(_: Request, exc: DomainError) -> JSONResponse:
        return _problem(exc.status_code, exc.title, exc.detail, exc.code, exc.extra)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
        return _problem(
            422, "Validation failed", "Request body or parameters are invalid", "REQUEST_INVALID", {"errors": errors}
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        return _problem(exc.status_code, "HTTP error", str(exc.detail), f"HTTP_{exc.status_code}")

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error")
        return _problem(500, "Internal error", "An unexpected error occurred", "INTERNAL_ERROR")

    for r in (auth.router, projects.router, documents.router, misc.router):
        app.include_router(r, prefix="/api/v1")
    return app


app = create_app()
