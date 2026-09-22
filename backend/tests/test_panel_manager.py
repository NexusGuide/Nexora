"""Panel credentials must survive a round trip and never leak in the open."""

from __future__ import annotations

import base64

import pytest

from app.core.exceptions import NotImplementedYetError, PanelError
from app.core.security import CredentialCipher
from app.models.enums import PanelType
from app.models.panel import Panel
from app.panels.base import PanelCredentials
from app.panels.manager import PanelManager
from app.panels.pasarguard import PasarGuardAdapter

KEY = base64.urlsafe_b64encode(b"m" * 32).decode()
OTHER_KEY = base64.urlsafe_b64encode(b"z" * 32).decode()


@pytest.fixture
def manager() -> PanelManager:
    return PanelManager(CredentialCipher(KEY))


def make_panel(manager: PanelManager, panel_type=PanelType.PASARGUARD) -> Panel:
    encrypted = manager.encrypt_credentials(
        username="panel-admin", password="panel-secret-pw", api_key=None
    )
    return Panel(
        id="panel-1",
        name="Germany",
        panel_type=panel_type,
        base_url="https://panel.example.com",
        verify_tls=True,
        timeout_seconds=20,
        **encrypted,
    )


def test_credentials_are_stored_encrypted(manager):
    panel = make_panel(manager)
    assert panel.encrypted_password is not None
    assert "panel-secret-pw" not in panel.encrypted_password
    assert panel.encrypted_username is not None
    assert "panel-admin" not in panel.encrypted_username
    assert panel.has_credentials


def test_credentials_round_trip_through_the_manager(manager):
    panel = make_panel(manager)
    creds = manager.credentials_for(panel)
    assert creds.username == "panel-admin"
    assert creds.password == "panel-secret-pw"
    assert creds.base_url == "https://panel.example.com"


def test_credentials_repr_does_not_leak():
    """A stray repr() in a traceback or log must not print the password."""
    creds = PanelCredentials(
        base_url="https://panel.example.com",
        username="panel-admin",
        password="panel-secret-pw",
    )
    for rendered in (repr(creds), str(creds), f"{creds}"):
        assert "panel-secret-pw" not in rendered
        assert "panel-admin" not in rendered
        assert "[REDACTED]" in rendered


def test_panel_repr_does_not_leak(manager):
    assert "panel-secret-pw" not in repr(make_panel(manager))


def test_wrong_key_produces_a_clear_actionable_error(manager):
    panel = make_panel(manager)
    other = PanelManager(CredentialCipher(OTHER_KEY))
    with pytest.raises(PanelError) as exc:
        other.credentials_for(panel)
    assert exc.value.code == "PANEL_CREDENTIALS_UNREADABLE"
    assert "ENCRYPTION_KEY" in exc.value.message


def test_pasarguard_adapter_is_built_for_a_pasarguard_panel(manager):
    adapter = manager.adapter_for(make_panel(manager))
    assert isinstance(adapter, PasarGuardAdapter)
    assert adapter.panel_type == "PASARGUARD"


@pytest.mark.parametrize(
    "panel_type", [PanelType.XUI, PanelType.MARZBAN, PanelType.CUSTOM]
)
def test_unimplemented_panels_fail_loudly_rather_than_faking_success(manager, panel_type):
    """Spec rule 67: no pretend integrations."""
    with pytest.raises(NotImplementedYetError):
        manager.adapter_for(make_panel(manager, panel_type))


def test_partial_update_does_not_blank_a_credential(manager):
    encrypted = manager.encrypt_credentials(username=None, password="new-pw")
    assert encrypted["encrypted_username"] is None
    assert encrypted["encrypted_password"] is not None
