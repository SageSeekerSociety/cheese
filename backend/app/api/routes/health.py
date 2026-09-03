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
async def health_check() -> dict[str, Any]:
    """Healthy means everything this boot was supposed to start is still running.

    Two ways for that to be false while the process answers requests normally. A
    module that fails to import is skipped in production so one bad file cannot
    take the app down, and the app then serves 404s for a whole group of
    endpoints. A periodic job whose loop ended keeps its place in the table
    while its sweep simply stops running. Neither reaches a caller as an error,
    so reporting them here is what turns them into something monitoring sees.
    """
    from app.main import FAILED_ROUTE_MODULES, RUNNING_JOBS

    problems: dict[str, Any] = {}
    if FAILED_ROUTE_MODULES:
        problems["unmounted"] = list(FAILED_ROUTE_MODULES)
    # A job whose loop ended stops happening, and nothing else notices: the
    # sweep simply never runs again. `interval_seconds` is what separates that
    # from a job this deployment does not run at all.
    stalled = [j.name for j in RUNNING_JOBS if j.interval_seconds > 0 and not j.alive]
    if stalled:
        problems["stalled_jobs"] = stalled
    if problems:
        return {"status": "degraded", **problems}
    return {"status": "ok"}


@router.get("/health/detailed", summary="Detailed health check")
async def detailed_health_check() -> dict[str, Any]:
    checks: dict[str, Any] = {}

    checks["database"] = await _check_database()
    checks["redis"] = await _check_redis()

    overall = (
        "healthy"
        if all(c.get("status") == "up" for c in checks.values())
        else "degraded"
    )
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
