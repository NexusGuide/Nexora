"""Panel manager — the only place that turns a stored panel row into a working
adapter.

Business logic calls ``panel_manager.adapter_for(panel)`` and then the adapter
interface. It never sees a panel type, a base URL or a credential.

Decryption happens here and nowhere else, so the set of code paths that can
hold a plaintext panel credential stays small enough to audit.
"""

from __future__ import annotations

import logging
from typing import Final

from app.core.exceptions import NotImplementedYetError, PanelError
from app.core.security import CredentialCipher, CredentialDecryptionError
from app.models.enums import PanelType
from app.models.panel import Panel
from app.panels.base import PanelAdapter, PanelCredentials
from app.panels.pasarguard import PasarGuardAdapter

logger = logging.getLogger(__name__)

# Panels the spec lists for later phases. They are registered as unimplemented
# rather than stubbed with fake behaviour (spec rule 67).
_UNIMPLEMENTED: Final[dict[PanelType, str]] = {
    PanelType.XUI: "X-UI adapter is planned for phase 8",
    PanelType.MARZBAN: "Marzban adapter is planned for phase 8",
    PanelType.CUSTOM: "Custom panel adapter must be provided by the operator",
}

_ADAPTERS: Final[dict[PanelType, type[PanelAdapter]]] = {
    PanelType.PASARGUARD: PasarGuardAdapter,
}


class PanelManager:
    def __init__(self, cipher: CredentialCipher | None = None) -> None:
        self._cipher = cipher or CredentialCipher.from_settings()

    def credentials_for(self, panel: Panel) -> PanelCredentials:
        """Decrypt a panel's credentials for the duration of one operation."""
        try:
            return PanelCredentials(
                base_url=panel.base_url,
                username=self._cipher.decrypt(panel.encrypted_username),
                password=self._cipher.decrypt(panel.encrypted_password),
                api_key=self._cipher.decrypt(panel.encrypted_api_key),
                verify_tls=panel.verify_tls,
                timeout_seconds=panel.timeout_seconds,
            )
        except CredentialDecryptionError as exc:
            logger.error(
                "panel_credential_decrypt_failed",
                extra={"extra_fields": {"panel_id": panel.id}},
            )
            raise PanelError(
                "Stored panel credentials cannot be decrypted with the current "
                "ENCRYPTION_KEY. Re-enter them in the admin panel.",
                code="PANEL_CREDENTIALS_UNREADABLE",
            ) from exc

    def adapter_for(self, panel: Panel) -> PanelAdapter:
        """Build the adapter for a panel row."""
        panel_type = PanelType(panel.panel_type)

        if reason := _UNIMPLEMENTED.get(panel_type):
            raise NotImplementedYetError(reason, code="PANEL_TYPE_UNSUPPORTED")

        adapter_cls = _ADAPTERS.get(panel_type)
        if adapter_cls is None:
            raise NotImplementedYetError(
                f"No adapter is registered for panel type {panel_type.value}",
                code="PANEL_TYPE_UNSUPPORTED",
            )
        return adapter_cls(self.credentials_for(panel))

    def encrypt_credentials(
        self,
        *,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, str | None]:
        """Encrypt credentials on their way into the database.

        Returns the column values to assign. ``None`` inputs stay ``None``, so a
        partial update does not silently blank a credential.
        """
        return {
            "encrypted_username": self._cipher.encrypt(username),
            "encrypted_password": self._cipher.encrypt(password),
            "encrypted_api_key": self._cipher.encrypt(api_key),
        }

    @staticmethod
    def supported_types() -> list[str]:
        return [t.value for t in _ADAPTERS]
