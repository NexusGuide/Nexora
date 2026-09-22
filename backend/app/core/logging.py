"""Structured JSON logging with mandatory secret redaction.

Spec rule 43: never log passwords, tokens, panel secrets or payment secrets.
That is enforced here by a filter attached to the root logger, so redaction
does not depend on every call site remembering to do it.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from typing import Any

REDACTED = "[REDACTED]"

# Request-scoped context, attached to every record by ``ContextFilter``.
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)

# Keys whose values are always redacted, wherever they appear in a dict.
SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "password_hash",
        "new_password",
        "old_password",
        "current_password",
        "confirm_password",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "api_key",
        "apikey",
        "secret",
        "client_secret",
        "jwt_secret",
        "jwt_refresh_secret",
        "encryption_key",
        "private_key",
        "authorization",
        "cookie",
        "set-cookie",
        "session",
        "csrf",
        "otp",
        "pin",
        "panel_password",
        "panel_username",
        "panel_api_key",
        "encrypted_password",
        "encrypted_username",
        "encrypted_api_key",
        "payment_api_key",
        "merchant_id",
        "card_number",
        "cvv",
        "admin_bootstrap_secret",
        "subscription_url",
    }
)

# Patterns redacted inside free-form message strings.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value / key: value / "key": "value"
    (
        re.compile(
            r"(?i)\b(" + "|".join(sorted(SENSITIVE_KEYS)) + r")\b"
            r'(\s*["\']?\s*[:=]\s*["\']?)([^\s,;"\'}\)]+)'
        ),
        r"\1\2" + REDACTED,
    ),
    # Bearer tokens
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]+"), "Bearer " + REDACTED),
    # Anything JWT-shaped
    (re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}"), REDACTED),
    # Inline credentials in a connection URL
    (re.compile(r"(?i)(://[^:/\s]+:)([^@\s]+)(@)"), r"\1" + REDACTED + r"\3"),
    # Telegram bot tokens (length varies slightly across issuing eras)
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_\-]{30,45}\b"), REDACTED),
)


def redact(value: Any, _depth: int = 0) -> Any:
    """Recursively redact sensitive values from any log payload."""
    if _depth > 6:
        return value
    if isinstance(value, dict):
        return {
            k: (
                REDACTED
                if isinstance(k, str) and k.lower() in SENSITIVE_KEYS
                else redact(v, _depth + 1)
            )
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return type(value)(redact(v, _depth + 1) for v in value)
    if isinstance(value, str):
        out = value
        for pattern, replacement in _PATTERNS:
            out = pattern.sub(replacement, out)
        return out
    return value


class RedactionFilter(logging.Filter):
    """Scrubs the message and structured extras of every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, (str, dict, list, tuple)):
            record.msg = redact(record.msg)
        if record.args:
            record.args = (
                redact(record.args)
                if isinstance(record.args, dict)
                else tuple(redact(a) for a in record.args)
            )
        if extra := getattr(record, "extra_fields", None):
            record.extra_fields = redact(extra)  # type: ignore[attr-defined]
        return True


class ContextFilter(logging.Filter):
    """Attaches request_id and user_id (spec rule 43) to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()  # type: ignore[attr-defined]
        record.user_id = user_id_ctx.get()  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line — greppable and machine-parseable."""

    _RESERVED = frozenset(
        {
            "args",
            "asctime",
            "created",
            "exc_info",
            "exc_text",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "msg",
            "name",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "stack_info",
            "thread",
            "threadName",
            "taskName",
            "extra_fields",
        }
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if rid := getattr(record, "request_id", None):
            payload["request_id"] = rid
        if uid := getattr(record, "user_id", None):
            payload["user_id"] = uid
        for key, value in record.__dict__.items():
            if (
                key not in self._RESERVED
                and not key.startswith("_")
                and key not in payload
            ):
                payload[key] = value
        if extra := getattr(record, "extra_fields", None):
            payload.update(extra)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Install handlers, filters and formatter on the root logger."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter()
        if json_output
        else logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s"
        )
    )
    handler.addFilter(RedactionFilter())
    handler.addFilter(ContextFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    # Uvicorn's own loggers must go through the same redaction path.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True

    # Access logs are emitted by our middleware, with request_id attached.
    logging.getLogger("uvicorn.access").disabled = True
