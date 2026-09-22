"""Health endpoints (spec rule 38).

These are public and therefore deliberately terse: they report whether a
dependency is reachable, never a hostname, a version or a driver error, since
that would hand a scanner a map of the deployment.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.api.deps import SessionDep
from app.core.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": get_settings().app_name}


@router.get("/health/database", summary="Database readiness")
async def health_database(session: SessionDep, response: Response) -> dict[str, str]:
    try:
        await session.execute(select(1))
    except Exception:
        logger.exception("health_database_failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "component": "database"}
    return {"status": "ok", "component": "database"}


@router.get("/health/redis", summary="Redis readiness")
async def health_redis(response: Response) -> dict[str, str]:
    settings = get_settings()
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url)
        try:
            await client.ping()
        finally:
            await client.aclose()
    except Exception:
        logger.exception("health_redis_failed")
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "component": "redis"}
    return {"status": "ok", "component": "redis"}


@router.get("/health/panels", summary="Panel readiness")
async def health_panels(session: SessionDep, response: Response) -> dict[str, object]:
    """Report each panel's last known state.

    Reports what was last recorded rather than testing live: a health endpoint
    that made an outbound call per panel would be a way to make the API slow,
    or to use it as an amplifier. The scheduler refreshes these.
    """
    from sqlalchemy import select as _select

    from app.models.enums import PanelStatus
    from app.models.panel import Panel

    panels = list(await session.scalars(_select(Panel)))
    healthy = [p for p in panels if p.status is PanelStatus.ACTIVE]

    if panels and not healthy:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ok" if (healthy or not panels) else "unavailable",
        "component": "panels",
        "total": len(panels),
        "healthy": len(healthy),
        # Names only. A base URL here would map the operator's infrastructure
        # for anyone who can reach the endpoint.
        "unhealthy": [p.name for p in panels if p.status is not PanelStatus.ACTIVE],
    }
