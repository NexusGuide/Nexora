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
