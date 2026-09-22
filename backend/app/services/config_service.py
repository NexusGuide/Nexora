"""Config storage and lifecycle (spec rule 8).

Configs come from a panel and are replaced wholesale on each refresh, rather
than merged: the panel is the source of truth, and a config it has stopped
issuing must stop being offered. Replacing preserves which one the user had
activated, so a refresh does not silently change their server.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError
from app.models.billing import Config, Subscription
from app.services.config_parser import is_probably_config, parse_config_uri

logger = logging.getLogger(__name__)

# A panel returning an absurd number of configs is misconfigured; storing them
# all would bloat the table and the mobile app's list.
MAX_CONFIGS_PER_SUBSCRIPTION = 50


class ConfigService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def replace_for_subscription(
        self,
        subscription_id: str,
        config_uris: list[str],
        *,
        server_id: str | None = None,
    ) -> list[Config]:
        """Replace a subscription's configs with what the panel now returns.

        Returns the stored configs. The previously active config keeps its
        active flag when it is still present, so a routine refresh does not
        move the user to a different server behind their back.
        """
        previous_active = await self.session.scalar(
            select(Config).where(
                Config.subscription_id == subscription_id,
                Config.is_active.is_(True),
            )
        )
        previously_active_data = previous_active.config_data if previous_active else None

        usable = [u.strip() for u in config_uris if is_probably_config(u)]
        skipped = len(config_uris) - len(usable)
        if skipped:
            logger.info(
                "config_entries_skipped",
                extra={
                    "extra_fields": {
                        "subscription_id": subscription_id,
                        "skipped": skipped,
                    }
                },
            )

        usable = usable[:MAX_CONFIGS_PER_SUBSCRIPTION]

        await self.session.execute(
            delete(Config).where(Config.subscription_id == subscription_id)
        )

        stored: list[Config] = []
        for uri in usable:
            parsed = parse_config_uri(uri)
            config = Config(
                subscription_id=subscription_id,
                server_id=server_id,
                name=parsed.name,
                protocol=parsed.protocol,
                host=parsed.host,
                port=parsed.port,
                config_data=uri,
                is_active=uri == previously_active_data,
            )
            self.session.add(config)
            stored.append(config)

        # Nothing active — because this is the first fetch, or because the
        # panel dropped the config the user had chosen. Fall back to the first.
        if stored and not any(c.is_active for c in stored):
            stored[0].is_active = True

        await self.session.flush()
        logger.info(
            "configs_replaced",
            extra={
                "extra_fields": {
                    "subscription_id": subscription_id,
                    "count": len(stored),
                }
            },
        )
        return stored

    async def list_for_user(
        self, user_id: str, *, subscription_id: str | None = None
    ) -> list[Config]:
        """Configs belonging to this user, across their subscriptions."""
        stmt = (
            select(Config)
            .join(Subscription, Config.subscription_id == Subscription.id)
            .where(Subscription.user_id == user_id)
        )
        if subscription_id is not None:
            stmt = stmt.where(Config.subscription_id == subscription_id)
        result = await self.session.scalars(
            stmt.order_by(Config.is_active.desc(), Config.name)
        )
        return list(result)

    async def get_for_user(self, config_id: str, user_id: str) -> Config:
        config = await self.session.scalar(
            select(Config)
            .join(Subscription, Config.subscription_id == Subscription.id)
            .where(Config.id == config_id, Subscription.user_id == user_id)
        )
        if config is None:
            raise NotFoundError("Config not found", code="CONFIG_NOT_FOUND")
        return config

    async def activate(self, config_id: str, user_id: str) -> Config:
        """Make one config the active one for its subscription."""
        config = await self.get_for_user(config_id, user_id)

        siblings = await self.session.scalars(
            select(Config).where(Config.subscription_id == config.subscription_id)
        )
        for sibling in siblings:
            sibling.is_active = sibling.id == config.id

        await self.session.flush()
        return config

    async def delete(self, config_id: str, user_id: str) -> None:
        """Remove a config the user does not want listed.

        The next refresh will bring it back if the panel still issues it —
        this hides a config, it does not revoke access to a server.
        """
        config = await self.get_for_user(config_id, user_id)
        was_active = config.is_active
        subscription_id = config.subscription_id

        await self.session.delete(config)
        await self.session.flush()

        if was_active:
            replacement = await self.session.scalar(
                select(Config)
                .where(Config.subscription_id == subscription_id)
                .order_by(Config.name)
            )
            if replacement is not None:
                replacement.is_active = True
                await self.session.flush()
