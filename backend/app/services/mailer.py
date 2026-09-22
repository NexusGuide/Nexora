"""Outbound email.

Two implementations behind one protocol. ``ConsoleTransport`` prints the
message instead of sending it, which is what development uses: it makes the
reset link visible without configuring a mail server, and it says plainly that
nothing was delivered. It is selected only when SMTP_HOST is unset.

``SMTPTransport`` sends for real. Credentials come from the environment, and
the module never logs the message body — a password-reset email contains a
token that is, for its lifetime, as good as the password.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class Message:
    to: str
    subject: str
    body: str


class MailTransport(Protocol):
    async def send(self, message: Message) -> bool: ...


class ConsoleTransport:
    """Prints instead of sending. Development only."""

    def __init__(self) -> None:
        self.sent: list[Message] = []

    async def send(self, message: Message) -> bool:
        self.sent.append(message)

        # The summary goes through logging, which redacts.
        logger.warning(
            "email_not_sent_console_transport",
            extra={
                "extra_fields": {
                    "to": message.to,
                    "subject": message.subject,
                    "note": "Configure SMTP_HOST to send real email",
                }
            },
        )

        # The body is printed directly rather than logged, because the log
        # filter redacts token-shaped strings — correctly, but that would
        # remove the one thing this transport exists to show. Printing is
        # safe *here* because this transport is only selected when SMTP_HOST
        # is unset, which is development.
        print(
            "\n"
            + "=" * 72
            + "\n  EMAIL NOT SENT — no SMTP_HOST configured\n"
            + f"  To:      {message.to}\n"
            + f"  Subject: {message.subject}\n"
            + "-" * 72
            + f"\n{message.body}\n"
            + "=" * 72
            + "\n",
            file=sys.stderr,
            flush=True,
        )
        return True


class SMTPTransport:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def send(self, message: Message) -> bool:
        import asyncio

        # smtplib is blocking, so it runs in a thread rather than stalling the
        # event loop for the duration of an SMTP conversation.
        return await asyncio.to_thread(self._send_blocking, message)

    def _send_blocking(self, message: Message) -> bool:
        s = self.settings
        email = EmailMessage()
        email["From"] = s.smtp_from or s.smtp_user
        email["To"] = message.to
        email["Subject"] = message.subject
        email.set_content(message.body)

        try:
            if s.smtp_use_ssl:
                server = smtplib.SMTP_SSL(
                    s.smtp_host,
                    s.smtp_port,
                    timeout=15,
                    context=ssl.create_default_context(),
                )
            else:
                server = smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15)
            with server:
                if s.smtp_use_tls and not s.smtp_use_ssl:
                    server.starttls(context=ssl.create_default_context())
                if s.smtp_user and s.smtp_password:
                    server.login(s.smtp_user, s.smtp_password)
                server.send_message(email)
        except Exception:
            # Logged without the body, and without the SMTP password that a
            # verbose smtplib traceback could otherwise expose.
            logger.exception(
                "email_send_failed",
                extra={"extra_fields": {"to": message.to, "host": s.smtp_host}},
            )
            return False

        logger.info("email_sent", extra={"extra_fields": {"to": message.to}})
        return True


def build_mailer(settings: Settings | None = None) -> MailTransport:
    settings = settings or get_settings()
    if settings.smtp_host:
        return SMTPTransport(settings)
    return ConsoleTransport()


def password_reset_message(to: str, reset_url: str, ttl_minutes: int) -> Message:
    return Message(
        to=to,
        subject="Reset your password",
        body=(
            "Someone asked to reset the password for your account.\n\n"
            f"{reset_url}\n\n"
            f"The link works once and expires in {ttl_minutes} minutes.\n\n"
            "If this wasn't you, you can ignore this message — your password "
            "has not changed."
        ),
    )


def email_verification_message(to: str, verify_url: str, ttl_hours: int) -> Message:
    return Message(
        to=to,
        subject="Confirm your email address",
        body=(
            "Confirm this address to finish setting up your account.\n\n"
            f"{verify_url}\n\n"
            f"The link works once and expires in {ttl_hours} hours."
        ),
    )
