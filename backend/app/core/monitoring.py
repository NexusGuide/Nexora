"""Error tracking and metrics.

Both are optional and off unless configured. Both are wired here rather than
in `main.py` so there is one place to check what leaves the process.

Sentry receives exceptions, which means it receives whatever is attached to
them: local variables, request bodies, headers. That is a copy of the
application's secrets sitting in a third-party service, so everything is
scrubbed on the way out and PII is off.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings
from app.core.logging import SENSITIVE_KEYS, redact

logger = logging.getLogger(__name__)

_metrics_enabled = False


# ------------------------------------------------------------------- Sentry
def _scrub_event(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any]:
    """Run every outbound Sentry event through the same redaction as logs.

    Sentry has its own scrubber, but it does not know about this application's
    field names — `encrypted_password`, `panel_api_key`, `subscription_url`.
    Ours does.
    """
    try:
        if request := event.get("request"):
            request.pop("data", None)  # bodies can contain a password
            request.pop("cookies", None)
            if headers := request.get("headers"):
                request["headers"] = {
                    k: ("[REDACTED]" if k.lower() in SENSITIVE_KEYS else v)
                    for k, v in headers.items()
                }
            if query := request.get("query_string"):
                request["query_string"] = redact(query)

        for entry in event.get("exception", {}).get("values", []):
            for frame in entry.get("stacktrace", {}).get("frames", []):
                if "vars" in frame:
                    frame["vars"] = redact(frame["vars"])

        if extra := event.get("extra"):
            event["extra"] = redact(extra)
        if message := event.get("message"):
            event["message"] = redact(message)
    except Exception:
        # A scrubber that raises would send the *unscrubbed* event, so drop it.
        logger.exception("sentry_scrub_failed")
        return {}
    return event


def init_sentry(settings: Settings) -> bool:
    if not settings.sentry_dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:
        logger.warning("sentry_sdk_not_installed")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.app_env,
        release="nexusvpn@0.0.1",
        # Never attach user identifiers, cookies or bodies automatically.
        send_default_pii=False,
        max_request_body_size="never",
        attach_stacktrace=True,
        # Sampled, not exhaustive: full tracing on a VPN API is a large volume
        # of data for little added insight.
        traces_sample_rate=0.05 if settings.is_production else 0.0,
        before_send=_scrub_event,
        integrations=[
            StarletteIntegration(transaction_style="endpoint"),
            FastApiIntegration(transaction_style="endpoint"),
        ],
    )
    logger.info("sentry_initialised", extra={"extra_fields": {"env": settings.app_env}})
    return True


# --------------------------------------------------------------- Prometheus
try:
    from prometheus_client import Counter, Histogram

    REQUESTS = Counter(
        "nexus_http_requests_total",
        "HTTP requests",
        ["method", "endpoint", "status"],
    )
    LATENCY = Histogram(
        "nexus_http_request_duration_seconds",
        "Request latency",
        ["method", "endpoint"],
        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    JOBS = Counter("nexus_jobs_total", "Background jobs processed", ["job", "outcome"])
    PROVISIONING = Counter(
        "nexus_provisioning_total", "Provisioning attempts", ["outcome"]
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _PROMETHEUS_AVAILABLE = False
    REQUESTS = LATENCY = JOBS = PROVISIONING = None  # type: ignore[assignment]


def init_metrics(settings: Settings) -> bool:
    global _metrics_enabled
    _metrics_enabled = bool(settings.metrics_enabled and _PROMETHEUS_AVAILABLE)
    if settings.metrics_enabled and not _PROMETHEUS_AVAILABLE:
        logger.warning("prometheus_client_not_installed")
    return _metrics_enabled


def metrics_enabled() -> bool:
    return _metrics_enabled


def record_request(
    method: str, endpoint: str, status: int, duration_seconds: float
) -> None:
    """Record one request.

    ``endpoint`` must be the route *template* (`/api/v1/orders/{order_id}`),
    never the resolved path: one label value per order id would produce
    unbounded cardinality and eventually take Prometheus down.
    """
    if not _metrics_enabled:
        return
    try:
        REQUESTS.labels(method=method, endpoint=endpoint, status=str(status)).inc()
        LATENCY.labels(method=method, endpoint=endpoint).observe(duration_seconds)
    except Exception:
        logger.debug("metric_record_failed", exc_info=True)


def record_job(job: str, outcome: str) -> None:
    if not _metrics_enabled:
        return
    try:
        JOBS.labels(job=job, outcome=outcome).inc()
    except Exception:
        logger.debug("metric_record_failed", exc_info=True)


def record_provisioning(outcome: str) -> None:
    if not _metrics_enabled:
        return
    try:
        PROVISIONING.labels(outcome=outcome).inc()
    except Exception:
        logger.debug("metric_record_failed", exc_info=True)
