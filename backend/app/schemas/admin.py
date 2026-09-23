"""Admin-only request contracts."""

from __future__ import annotations

from pydantic import BaseModel, Field


class BootstrapRequest(BaseModel):
    """Claim OWNER on a fresh deployment.

    `secret` is `ADMIN_BOOTSTRAP_SECRET` from the server's environment, and
    `identifier` is the username or email of an account that has already
    registered normally.
    """

    secret: str = Field(..., min_length=32, max_length=512)
    identifier: str = Field(..., min_length=1, max_length=255)
