"""Request middleware: request IDs, access logging, security headers."""

from __future__ import annotations

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.logging import request_id_ctx, user_id_ctx
from app.core.monitoring import record_request

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
            duration = time.perf_counter() - started
            # The route template, never the resolved path: one label value per
            # order id would blow up Prometheus cardinality.
            route = request.scope.get("route")
            endpoint = getattr(route, "path", None) or "unmatched"
            record_request(request.method, endpoint, status_code, duration)

            logger.info(
                "request",
                extra={
                    "extra_fields": {
                        "endpoint": request.url.path,
                        "method": request.method,
                        "status_code": status_code,
                        "latency_ms": round(duration * 1000, 2),
                        "client_ip": request.client.host if request.client else None,
                    }
                },
            )
            request_id_ctx.reset(token)
            user_id_ctx.reset(user_token)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies the rate limit before the route runs.

    Placed after RequestContextMiddleware so a rejection still carries a
    request_id and appears in the access log.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        from app.api.rate_limit import enforce
        from app.core.exceptions import RateLimitedError

        try:
            await enforce(request)
        except RateLimitedError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "success": False,
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        **({"details": exc.details} if exc.details else {}),
                    },
                    "request_id": getattr(request.state, "request_id", None),
                },
                headers={"Retry-After": str(exc.details.get("window_seconds", 60))},
            )
        return await call_next(request)


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
