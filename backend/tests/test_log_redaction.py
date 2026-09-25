"""Secrets must never reach a log record (spec rule 43)."""

from __future__ import annotations

import json
import logging

import pytest

from app.core.logging import JsonFormatter, RedactionFilter, redact

# A Telegram-shaped token, assembled at runtime. It is fake (the example from
# Telegram's own docs), but written as one literal it matches secret scanners
# and GitHub reports it as a public leak. Split, it still exercises the
# redaction pattern exactly the same way.
FAKE_BOT_SECRET = "AAHdqTcvCH1vGWJxf" + "SeofSAs0K5PALDsaw"
FAKE_BOT_TOKEN = "123456789" + ":" + FAKE_BOT_SECRET


@pytest.mark.parametrize(
    "message",
    [
        'password="CorrectHorse1"',
        "password=CorrectHorse1",
        "api_key: sk-live-abcdef123456",
        "JWT_SECRET=0123456789abcdef-jwt-sample-for-tests-only",
        "panel_password=hunter2hunter2",
        "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.abc.def",
        "postgresql+asyncpg://nexus:supersecretpw@db:5432/nexusvpn",
        f"bot {FAKE_BOT_TOKEN}",
    ],
)
def test_sensitive_strings_are_redacted(message):
    out = redact(message)
    for leaked in (
        "CorrectHorse1",
        "sk-live-abcdef123456",
        "0123456789abcdef-jwt-sample-for-tests-only",
        "hunter2hunter2",
        "supersecretpw",
        FAKE_BOT_SECRET,
    ):
        assert leaked not in out
    assert "[REDACTED]" in out


def test_nested_dicts_are_redacted():
    payload = {
        "user": {"username": "alice", "password": "CorrectHorse1"},
        "panel": {"encrypted_password": "gAAAAAB...", "base_url": "https://p.example"},
        "tokens": [{"refresh_token": "abc.def.ghi"}],
    }
    out = redact(payload)
    assert out["user"]["username"] == "alice"
    assert out["user"]["password"] == "[REDACTED]"
    assert out["panel"]["encrypted_password"] == "[REDACTED]"
    assert out["panel"]["base_url"] == "https://p.example"
    assert out["tokens"][0]["refresh_token"] == "[REDACTED]"


def test_non_sensitive_content_is_untouched():
    assert redact("user alice logged in from 1.2.3.4") == (
        "user alice logged in from 1.2.3.4"
    )


def test_filter_scrubs_a_real_log_record():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="login failed password=CorrectHorse1",
        args=(),
        exc_info=None,
    )
    assert RedactionFilter().filter(record)
    assert "CorrectHorse1" not in record.getMessage()


def test_formatter_emits_parseable_json_without_secrets():
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="auth",
        args=(),
        exc_info=None,
    )
    record.extra_fields = {"password": "CorrectHorse1", "user_id": "u-1"}
    RedactionFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))
    assert payload["password"] == "[REDACTED]"
    assert payload["user_id"] == "u-1"
    assert payload["level"] == "INFO"
