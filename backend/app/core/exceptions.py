"""Application error types mapped to the standard error envelope.

Error messages are written for a client. They never disclose whether an
account exists, which credential was wrong, or anything about panel internals.
"""

from __future__ import annotations

from typing import Any


class AppError(Exception):
    """Base class for errors the API converts into the error envelope."""

    status_code: int = 400
    code: str = "BAD_REQUEST"
    message: str = "Request could not be processed"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details
        super().__init__(self.message)


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"
    message = "Request validation failed"


class AuthenticationError(AppError):
    status_code = 401
    code = "AUTHENTICATION_FAILED"
    # Deliberately identical for a wrong username and a wrong password, so the
    # response cannot be used to enumerate accounts.
    message = "Invalid credentials"


class TokenExpiredError(AppError):
    status_code = 401
    code = "TOKEN_EXPIRED"
    message = "Token has expired"


class TokenInvalidError(AppError):
    status_code = 401
    code = "TOKEN_INVALID"
    message = "Token is invalid"


class PermissionDeniedError(AppError):
    status_code = 403
    code = "PERMISSION_DENIED"
    message = "You do not have permission to perform this action"


class AccountLockedError(AppError):
    status_code = 429
    code = "ACCOUNT_LOCKED"
    message = "Too many failed attempts. Try again later."


class AccountInactiveError(AppError):
    status_code = 403
    code = "ACCOUNT_INACTIVE"
    message = "This account is not active"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"
    message = "Resource not found"


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"
    message = "Resource already exists"


class RateLimitedError(AppError):
    status_code = 429
    code = "RATE_LIMITED"
    message = "Too many requests"


class PanelError(AppError):
    """A panel rejected or failed a request.

    The upstream panel's own message is never forwarded verbatim to a client:
    it can contain hostnames, tokens or internal paths.
    """

    status_code = 502
    code = "PANEL_ERROR"
    message = "Panel operation failed"


class NotImplementedYetError(AppError):
    """For an interface that exists but has no implementation yet (rule 67)."""

    status_code = 501
    code = "NOT_IMPLEMENTED"
    message = "This capability is not implemented yet"
