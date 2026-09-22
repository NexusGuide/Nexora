"""Panel and server selection, and panel health.

Selection is deliberately simple — least-loaded healthy server on an active
panel. "Auto server selection" as a feature (spec rule 70) can replace this
strategy without touching provisioning, because provisioning asks this service
for a target rather than choosing one itself.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError, PanelError
from app.models.billing import PlanServer
from app.models.enums import PanelStatus, PanelType, ServerStatus
from app.models.panel import AuditLog, Panel, Server
from app.panels.manager import PanelManager

logger = logging.getLogger(__name__)


class PanelService:
    def __init__(
        self, session: AsyncSession, manager: PanelManager | None = None
    ) -> None:
        self.session = session
        self.manager = manager or PanelManager()

    # ------------------------------------------------------------ selection
    async def select_server(self, plan_id: str) -> tuple[Panel, Server]:
        """Pick where to provision a subscription for this plan.

        A plan may be pinned to specific servers; if it is not, any healthy
        server is eligible.
        """
        pinned = await self.session.scalars(
            select(PlanServer.server_id).where(PlanServer.plan_id == plan_id)
        )
        pinned_ids = list(pinned)

        stmt = (
            select(Server)
            .join(Panel, Server.panel_id == Panel.id)
            .where(
                Server.status == ServerStatus.ACTIVE,
                Server.is_healthy.is_(True),
                Panel.status == PanelStatus.ACTIVE,
            )
            .order_by(Server.load_percent)
        )
        if pinned_ids:
            stmt = stmt.where(Server.id.in_(pinned_ids))

        server = await self.session.scalar(stmt)
        if server is None:
            # A real operational condition, not a bug: every server is down,
            # in maintenance, or the plan is pinned to servers that are.
            raise ConflictError(
                "No server is currently available for this plan",
                code="NO_SERVER_AVAILABLE",
                status_code=503,
            )

        panel = await self.session.get(Panel, server.panel_id)
        if panel is None:
            raise ConflictError(
                "The selected server has no panel configured",
                code="PANEL_MISSING",
                status_code=503,
            )
        return panel, server

    # --------------------------------------------------------------- health
    async def check_panel(self, panel_id: str) -> tuple[bool, str | None]:
        """Test a panel's credentials and reachability.

        Records the outcome on the panel row so the admin panel and
        ``/health/panels`` can report it without re-testing every time.
        """
        panel = await self.session.get(Panel, panel_id)
        if panel is None:
            raise NotFoundError("Panel not found", code="PANEL_NOT_FOUND")

        panel.last_checked_at = datetime.now(UTC)
        try:
            adapter = self.manager.adapter_for(panel)
            async with adapter:
                await adapter.test_connection()
        except PanelError as exc:
            panel.status = PanelStatus.UNREACHABLE
            # exc.message is ours, written for a client; the upstream panel's
            # own body is never stored here.
            panel.last_error = exc.message[:500]
            await self.session.flush()
            logger.warning(
                "panel_check_failed",
                extra={"extra_fields": {"panel_id": panel_id, "code": exc.code}},
            )
            return False, exc.message
        except Exception as exc:
            panel.status = PanelStatus.UNREACHABLE
            panel.last_error = type(exc).__name__
            await self.session.flush()
            logger.exception(
                "panel_check_error", extra={"extra_fields": {"panel_id": panel_id}}
            )
            return False, "Panel check failed"

        if panel.status is PanelStatus.UNREACHABLE:
            panel.status = PanelStatus.ACTIVE
        panel.last_error = None
        await self.session.flush()
        return True, None

    async def check_all(self) -> dict[str, bool]:
        panels = await self.session.scalars(
            select(Panel).where(Panel.status != PanelStatus.DISABLED)
        )
        results: dict[str, bool] = {}
        for panel in panels:
            ok, _ = await self.check_panel(panel.id)
            results[panel.name] = ok
        return results

    # ------------------------------------------------------------ mutation
    async def create_panel(
        self,
        *,
        name: str,
        panel_type: PanelType,
        base_url: str,
        username: str | None,
        password: str | None,
        api_key: str | None,
        verify_tls: bool = True,
        actor_id: str | None = None,
        ip_address: str | None = None,
    ) -> Panel:
        """Register a panel, storing its credentials encrypted."""
        clash = await self.session.scalar(select(Panel).where(Panel.name == name))
        if clash is not None:
            raise ConflictError(
                "A panel with that name already exists", code="PANEL_EXISTS"
            )

        encrypted = self.manager.encrypt_credentials(
            username=username, password=password, api_key=api_key
        )
        panel = Panel(
            name=name,
            panel_type=panel_type,
            base_url=base_url.rstrip("/"),
            verify_tls=verify_tls,
            status=PanelStatus.ACTIVE,
            **encrypted,
        )
        self.session.add(panel)
        await self.session.flush()

        await self.record_audit(
            actor_id=actor_id,
            action="panel.create",
            entity="panel",
            entity_id=panel.id,
            ip_address=ip_address,
            # Deliberately records only the non-secret fields. An audit log
            # that captured the credential would undo the encryption at rest.
            metadata={"name": name, "type": panel_type.value, "base_url": base_url},
        )
        return panel

    async def update_credentials(
        self,
        panel_id: str,
        *,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        actor_id: str | None = None,
        ip_address: str | None = None,
    ) -> Panel:
        panel = await self.session.get(Panel, panel_id)
        if panel is None:
            raise NotFoundError("Panel not found", code="PANEL_NOT_FOUND")

        encrypted = self.manager.encrypt_credentials(
            username=username, password=password, api_key=api_key
        )
        # Only overwrite what was supplied: a partial update must not blank a
        # credential the caller did not mention.
        for column, value in encrypted.items():
            if value is not None:
                setattr(panel, column, value)

        await self.session.flush()
        await self.record_audit(
            actor_id=actor_id,
            action="panel.update_credentials",
            entity="panel",
            entity_id=panel.id,
            ip_address=ip_address,
            metadata={"fields": [k for k, v in encrypted.items() if v is not None]},
        )
        return panel

    # ----------------------------------------------------------- audit log
    async def record_audit(
        self,
        *,
        actor_id: str | None,
        action: str,
        entity: str,
        entity_id: str | None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        from app.core.logging import redact

        self.session.add(
            AuditLog(
                actor_id=actor_id,
                action=action,
                entity=entity,
                entity_id=entity_id,
                ip_address=ip_address,
                user_agent=(user_agent or "")[:255] or None,
                # Redacted on the way in: an audit entry is written by many
                # call sites and must not become a place secrets accumulate.
                metadata_json=(
                    json.dumps(redact(metadata), ensure_ascii=False, default=str)
                    if metadata
                    else None
                ),
                created_at=datetime.now(UTC),
            )
        )
        await self.session.flush()
