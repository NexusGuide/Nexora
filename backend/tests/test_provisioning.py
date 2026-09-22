"""Provisioning: paid order to working VPN account, and what happens when the
panel is down."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.core.exceptions import ConflictError, PanelError
from app.core.security import CredentialCipher
from app.models.billing import Config, Plan, Subscription
from app.models.enums import (
    PanelStatus,
    PanelType,
    PlanStatus,
    ServerStatus,
    SubscriptionStatus,
)
from app.models.panel import Panel, Server
from app.panels.base import PanelAdapter, PanelUsage, PanelUser
from app.panels.manager import PanelManager
from app.schemas.auth import RegisterRequest
from app.services.auth_service import AuthService
from app.services.order_service import OrderService
from app.services.provisioning_service import ProvisioningService

GB = 1024**3
KEY = base64.urlsafe_b64encode(b"p" * 32).decode()


class FakeAdapter(PanelAdapter):
    """In-memory panel. Fails on demand, and counts calls."""

    panel_type = "PASARGUARD"

    def __init__(self, credentials, *, fail_create=False, configs=None):
        super().__init__(credentials)
        self.fail_create = fail_create
        self.users: dict[str, PanelUser] = {}
        self.create_calls = 0
        self.disabled: list[str] = []
        self._configs = (
            configs
            if configs is not None
            else [
                "vless://uuid-a@de1.example.com:443?type=ws#Germany",
                "vless://uuid-b@nl1.example.com:8443?type=grpc#Netherlands",
            ]
        )

    async def test_connection(self) -> bool:
        return True

    async def create_user(self, username, **kw) -> PanelUser:
        if self.fail_create:
            raise PanelError("Panel is unreachable", code="PANEL_UNREACHABLE")
        self.create_calls += 1
        if username not in self.users:
            self.users[username] = PanelUser(
                username=username,
                status="active",
                traffic_limit_bytes=kw.get("traffic_limit_bytes", 0),
                subscription_url=f"https://panel.example/sub/{username}",
                configs=list(self._configs),
            )
        return self.users[username]

    async def get_user(self, username):
        return self.users.get(username)

    async def update_user(self, username, **changes):
        return self.users[username]

    async def delete_user(self, username) -> bool:
        self.users.pop(username, None)
        return True

    async def disable_user(self, username) -> bool:
        self.disabled.append(username)
        return True

    async def enable_user(self, username) -> bool:
        return True

    async def get_usage(self, username) -> PanelUsage:
        user = self.users[username]
        return PanelUsage(
            username=username,
            used_bytes=user.traffic_used_bytes,
            limit_bytes=user.traffic_limit_bytes,
            measured_at=datetime.now(UTC),
        )

    async def get_configs(self, username) -> list[str]:
        return list(self._configs)

    async def renew_user(self, username, **kw) -> PanelUser:
        return self.users[username]


class FakeManager(PanelManager):
    def __init__(self, adapter: FakeAdapter):
        super().__init__(CredentialCipher(KEY))
        self._adapter = adapter

    def adapter_for(self, panel):
        return self._adapter


async def build_world(session, *, healthy_server=True):
    user = await AuthService(session).register(
        RegisterRequest(username="buyer", password="CorrectHorse1")
    )
    plan = Plan(
        name="100GB / 30 days",
        duration_days=30,
        traffic_limit_bytes=100 * GB,
        device_limit=3,
        price=Decimal("249000"),
        currency="IRT",
        status=PlanStatus.ACTIVE,
    )
    cipher = CredentialCipher(KEY)
    panel = Panel(
        name="DE panel",
        panel_type=PanelType.PASARGUARD,
        base_url="https://panel.example.com",
        encrypted_username=cipher.encrypt("admin"),
        encrypted_password=cipher.encrypt("panel-secret"),
        status=PanelStatus.ACTIVE,
    )
    session.add_all([plan, panel])
    await session.flush()

    server = Server(
        panel_id=panel.id,
        name="de-1",
        host="de1.example.com",
        port=443,
        region="Germany",
        status=ServerStatus.ACTIVE if healthy_server else ServerStatus.MAINTENANCE,
        is_healthy=healthy_server,
    )
    session.add(server)
    await session.commit()
    return user, plan, panel, server


async def paid_order(session, user, plan, key="buy-1"):
    service = OrderService(session)
    order, _ = await service.create_order(
        user_id=user.id, plan_id=plan.id, client_idempotency_key=key
    )
    await session.commit()
    await service.mark_paid(order.id)
    await session.commit()
    return order


# --- happy path -------------------------------------------------------------
async def test_paid_order_becomes_an_active_subscription_with_configs(session):
    user, plan, panel, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(adapter))
    result = await service.provision_order(order.id)
    await session.commit()

    subscription = await session.get(Subscription, result.subscription_id)
    assert subscription.status is SubscriptionStatus.ACTIVE
    assert subscription.panel_id == panel.id
    assert subscription.panel_username.startswith("nx_")
    assert subscription.expire_at is not None
    assert result.config_count == 2

    configs = list(
        await session.scalars(
            select(Config).where(Config.subscription_id == subscription.id)
        )
    )
    assert len(configs) == 2
    assert sum(1 for c in configs if c.is_active) == 1
    # The parser filled in display metadata from the URI.
    germany = next(c for c in configs if c.name == "Germany")
    assert germany.host == "de1.example.com"
    assert germany.port == 443
    assert germany.protocol == "vless/ws"


async def test_provisioning_is_idempotent(session):
    """The worker retries; that must not create a second panel account."""
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(adapter))

    first = await service.provision_order(order.id)
    await session.commit()
    second = await service.provision_order(order.id)
    await session.commit()
    third = await service.provision_order(order.id)
    await session.commit()

    assert first.activated is True
    assert second.already_active is True and third.already_active is True
    assert first.subscription_id == second.subscription_id
    assert adapter.create_calls == 1

    count = await session.scalar(select(func.count()).select_from(Subscription))
    assert count == 1
    # Configs were replaced, not appended, on each re-run.
    configs = await session.scalar(select(func.count()).select_from(Config))
    assert configs == 2


# --- failure handling -------------------------------------------------------
async def test_panel_failure_leaves_the_subscription_pending(session):
    """Never an ACTIVE subscription with nothing behind it."""
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None, fail_create=True)
    service = ProvisioningService(session, manager=FakeManager(adapter))

    with pytest.raises(PanelError):
        await service.provision_order(order.id)
    await session.commit()

    subscription = await session.scalar(select(Subscription))
    assert subscription.status is SubscriptionStatus.PENDING
    assert subscription.provisioning_error is not None
    assert subscription.expire_at is None


async def test_retry_after_failure_reuses_the_same_panel_username(session):
    """Otherwise each retry would orphan an account on the panel."""
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    failing = FakeAdapter(None, fail_create=True)
    service = ProvisioningService(session, manager=FakeManager(failing))
    with pytest.raises(PanelError):
        await service.provision_order(order.id)
    await session.commit()

    subscription = await session.scalar(select(Subscription))
    username_after_failure = subscription.panel_username
    assert username_after_failure is not None

    working = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(working))
    result = await service.provision_order(order.id)
    await session.commit()

    assert result.activated is True
    assert subscription.panel_username == username_after_failure
    assert list(working.users) == [username_after_failure]


async def test_no_healthy_server_is_a_clear_503(session):
    user, plan, _, _ = await build_world(session, healthy_server=False)
    order = await paid_order(session, user, plan)

    service = ProvisioningService(session, manager=FakeManager(FakeAdapter(None)))
    with pytest.raises(ConflictError) as exc:
        await service.provision_order(order.id)
    assert exc.value.code == "NO_SERVER_AVAILABLE"
    assert exc.value.status_code == 503


async def test_unpaid_order_is_not_provisioned(session):
    user, plan, _, _ = await build_world(session)
    order, _ = await OrderService(session).create_order(user_id=user.id, plan_id=plan.id)
    await session.commit()

    service = ProvisioningService(session, manager=FakeManager(FakeAdapter(None)))
    with pytest.raises(ConflictError) as exc:
        await service.provision_order(order.id)
    assert exc.value.code == "ORDER_NOT_PAID"


# --- refresh and sync -------------------------------------------------------
async def test_refresh_replaces_configs_and_keeps_the_active_one(session):
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(adapter))
    result = await service.provision_order(order.id)
    await session.commit()

    # Make the second config the active one.
    configs = list(
        await session.scalars(
            select(Config).where(Config.subscription_id == result.subscription_id)
        )
    )
    chosen = next(c for c in configs if c.name == "Netherlands")
    for c in configs:
        c.is_active = c.id == chosen.id
    await session.commit()
    chosen_uri = chosen.config_data

    await service.refresh_configs(result.subscription_id)
    await session.commit()

    after = list(
        await session.scalars(
            select(Config).where(Config.subscription_id == result.subscription_id)
        )
    )
    assert len(after) == 2
    active = [c for c in after if c.is_active]
    assert len(active) == 1
    # The user's chosen server survived the refresh.
    assert active[0].config_data == chosen_uri


async def test_refresh_drops_configs_the_panel_no_longer_issues(session):
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(adapter))
    result = await service.provision_order(order.id)
    await session.commit()

    adapter._configs = ["vless://uuid-c@fi1.example.com:443?type=ws#Finland"]
    await service.refresh_configs(result.subscription_id)
    await session.commit()

    after = list(
        await session.scalars(
            select(Config).where(Config.subscription_id == result.subscription_id)
        )
    )
    assert len(after) == 1
    assert after[0].name == "Finland"
    # Something must be active even after the chosen one disappeared.
    assert after[0].is_active is True


async def test_usage_sync_writes_through_to_the_subscription(session):
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(adapter))
    result = await service.provision_order(order.id)
    await session.commit()

    subscription = await session.get(Subscription, result.subscription_id)
    adapter.users[subscription.panel_username].traffic_used_bytes = 42 * GB

    used = await service.sync_usage(subscription.id)
    await session.commit()

    assert used == 42 * GB
    assert subscription.traffic_used_bytes == 42 * GB
    assert subscription.last_synced_at is not None


async def test_expiry_disables_the_panel_account(session):
    """The panel account must go down before the subscription is marked dead."""
    user, plan, _, _ = await build_world(session)
    order = await paid_order(session, user, plan)

    adapter = FakeAdapter(None)
    service = ProvisioningService(session, manager=FakeManager(adapter))
    result = await service.provision_order(order.id)
    await session.commit()

    subscription = await session.get(Subscription, result.subscription_id)
    subscription.expire_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()

    assert await service.disable_on_panel(subscription.id) is True
    assert subscription.panel_username in adapter.disabled
