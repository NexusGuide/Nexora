"""Nexus VPN backend entrypoint.

Boot order matters: settings are validated first, so a misconfigured or
placeholder secret stops the process here rather than surfacing later as a
runtime failure with a weak key already in use.
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.middleware import RequestContextMiddleware, SecurityHeadersMiddleware
from app.api.queue import close_queue
from app.api.v1 import admin as admin_routes
from app.api.v1 import auth as auth_routes
from app.api.v1 import configs as config_routes
from app.api.v1 import health as health_routes
from app.api.v1 import store as store_routes
from app.api.v1 import users as user_routes
from app.core.exceptions import AppError
from app.core.logging import configure_logging

try:
    from app.core.config import get_settings

    settings = get_settings()
except Exception as exc:
    # The message names the offending variable but never prints its value.
    print(
        "\n".join(
            [
                "",
                "=" * 72,
                " Nexus VPN failed to start: configuration is invalid.",
                "=" * 72,
                str(exc),
                "",
                " Fix your .env (start from .env.example, then run",
                " ./scripts/generate-secrets.sh) and try again.",
                "=" * 72,
                "",
            ]
        ),
        file=sys.stderr,
    )
    raise SystemExit(1) from exc

configure_logging(settings.log_level, json_output=not settings.app_debug)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "application_start",
        extra={"extra_fields": {"env": settings.app_env, "debug": settings.app_debug}},
    )
    yield
    await close_queue()
    logger.info("application_stop")


app = FastAPI(
    title=f"{settings.app_name} API",
    version="0.0.1",
    lifespan=lifespan,
    # API docs are useful in development and an unnecessary disclosure in
    # production, where the schema is published deliberately instead.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware, hsts=settings.is_production)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    max_age=600,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_host_list)


# ------------------------------------------------------------- error handlers
def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
                **({"details": exc.details} if exc.details else {}),
            },
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Field names and constraints are returned; submitted values are not, since
    # a rejected body can contain a password.
    fields = [
        {
            "field": ".".join(str(p) for p in err.get("loc", ())[1:]),
            "message": err.get("msg", "invalid"),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": {"fields": fields},
            },
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    # Full detail goes to the (redacted) log; the client gets a request_id to
    # quote, never a stack trace or a driver message.
    logger.exception(
        "unhandled_exception",
        extra={"extra_fields": {"path": request.url.path}},
    )
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
            },
            "request_id": _request_id(request),
        },
    )


# -------------------------------------------------------------------- routes
app.include_router(health_routes.router)
app.include_router(auth_routes.router, prefix=settings.api_v1_prefix)
app.include_router(user_routes.router, prefix=settings.api_v1_prefix)
app.include_router(store_routes.router, prefix=settings.api_v1_prefix)
app.include_router(config_routes.router, prefix=settings.api_v1_prefix)
app.include_router(admin_routes.router, prefix=settings.api_v1_prefix)


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {"service": settings.app_name, "api": settings.api_v1_prefix}
