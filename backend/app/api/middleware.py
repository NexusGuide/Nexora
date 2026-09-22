"""Request middleware: request IDs, access logging, security headers."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.logging import request_id_ctx, user_id_ctx

logger = logging.getLogger("app.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request_id and emits one structured access log per request.

    The log carries request_id, user_id, endpoint, status_code and latency
    (spec rule 43) and nothing from the request body.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        token = request_id_ctx.set(request_id)
        user_token = user_id_ctx.set(None)
        request.state.request_id = request_id

        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            logger.info(
                "request",
                extra={
                    "extra_fields": {
                        "endpoint": request.url.path,
                        "method": request.method,
                        "status_code": status_code,
                        "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                        "client_ip": request.client.host if request.client else None,
                    }
                },
            )
            request_id_ctx.reset(token)
            user_id_ctx.reset(user_token)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds the standard hardening headers (spec rule 37)."""

    def __init__(self, app, *, hsts: bool = False) -> None:
        super().__init__(app)
        self.hsts = hsts

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        # The API serves JSON only; nothing should ever be executed or framed.
        response.headers.setdefault(
            "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
        )
        if self.hsts:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        return response
