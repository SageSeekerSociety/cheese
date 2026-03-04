from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal


logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/healthz", summary="Health check")
async def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/detailed", summary="Detailed health check")
async def detailed_health_check() -> dict[str, Any]:
    checks: dict[str, Any] = {}

    checks["database"] = await _check_database()
    checks["redis"] = await _check_redis()

    overall = "healthy" if all(c.get("status") == "up" for c in checks.values()) else "degraded"
    return {"status": overall, "checks": checks}


async def _check_database() -> dict[str, Any]:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "up"}
    except Exception as e:
        logger.warning("Database health check failed: %s", e)
        return {"status": "down", "error": str(e)}


async def _check_redis() -> dict[str, Any]:
    try:
        redis = AsyncRedis.from_url(settings.redis_url)
        try:
            await redis.ping()
            return {"status": "up"}
        finally:
            await redis.aclose()
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)
        return {"status": "down", "error": str(e)}


@router.get("/metrics", summary="Application metrics")
async def get_metrics() -> dict[str, Any]:
    from app.core.metrics import registry

    return registry.export()


@router.get("/readyz", summary="Readiness check")
async def readiness_check() -> dict[str, Any]:
    result = await detailed_health_check()
    if result["status"] != "healthy":
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=result)
    return {"status": "ready"}
