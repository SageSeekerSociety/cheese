import logging
from typing import Any

from fastapi import APIRouter
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

# Which entries of `checks` decide whether this process should take traffic.
# Everything else in there is reported for a human and for alerting, which is a
# different question: a process that can still answer every request is not
# unready because one feature is degraded. Keeping the two apart is what lets a
# check be loud without also being a switch that pulls the whole platform out
# of rotation — see `memory` below.
_REQUIRED_CHECKS = ("database", "redis")

# A check that had nothing to do is not a failing check.
_HEALTHY_STATUSES = frozenset({"up", "skipped"})


@router.get("/healthz", summary="Health check")
async def health_check() -> dict[str, Any]:
    """Healthy means every route module mounted, not merely that the process is up.

    A module that fails to import is skipped in production so one bad file cannot
    take the app down — but the app is then serving 404s for a whole group of
    endpoints, and the only party that finds out is the caller. Reporting it here
    is what turns that into something monitoring can see.
    """
    from app.main import FAILED_ROUTE_MODULES

    if FAILED_ROUTE_MODULES:
        return {"status": "degraded", "unmounted": list(FAILED_ROUTE_MODULES)}
    return {"status": "ok"}


@router.get("/health/detailed", summary="Detailed health check")
async def detailed_health_check() -> dict[str, Any]:
    checks: dict[str, Any] = {}

    checks["database"] = await _check_database()
    checks["redis"] = await _check_redis()
    checks["memory"] = await _check_memory()
    checks["event_loop"] = _check_event_loop()

    overall = (
        "healthy"
        if all(c.get("status") in _HEALTHY_STATUSES for c in checks.values())
        else "degraded"
    )
    return {"status": overall, "checks": checks}


def _check_event_loop() -> dict[str, Any]:
    """How late this process's event loop is running.

    Reported, never required: a stalling loop is something to chase, not a
    reason to take the process out of rotation. `redis` above goes down for a
    stall of a few seconds — the read times out — which is how the cause used
    to be read as Redis being unwell.
    """
    from app.core.loop_lag import STALL_S, lag_status

    lag = lag_status()
    return {
        "status": "up" if lag["recent_ms"] < STALL_S * 1000 else "stalling",
        **lag,
    }


async def _check_database() -> dict[str, Any]:
    from app.core.db import pool_status

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "up", "pool": pool_status()}
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


async def _check_memory() -> dict[str, Any]:
    """Can the memory backend reach its model endpoints, with the configured key?

    This is the only place that answers that at all. On the openviking backend
    extraction runs in a background task, so a rejected key produces no
    user-visible symptom whatsoever — the platform just stops learning, exactly
    as if it were still on the db backend.

    The import is deferred because a db deployment must not pay for the
    openviking config path; the probe behind it is cached and refreshed off the
    request path, so this never waits on the model vendor.
    """
    try:
        from app.domain.memory.endpoint_probe import memory_backend_health

        return await memory_backend_health()
    except Exception as e:  # noqa: BLE001 — a health check reports, never raises
        logger.warning("Memory health check failed: %s", e)
        return {"status": "down", "error": str(e)}


@router.get("/metrics", summary="Application metrics")
async def get_metrics() -> dict[str, Any]:
    from app.core.metrics import registry

    return registry.export()


@router.get("/readyz", summary="Readiness check")
async def readiness_check() -> dict[str, Any]:
    """Ready means "can serve requests", which is narrower than "all green".

    Only `_REQUIRED_CHECKS` can make this 503. A degraded advisory check still
    shows up in `/health/detailed` — that is where a human or an alert looks —
    but taking the process out of rotation over it would trade one degraded
    feature for a total outage.
    """
    result = await detailed_health_check()
    unready = [
        name
        for name in _REQUIRED_CHECKS
        if result["checks"].get(name, {}).get("status") != "up"
    ]
    if unready:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail=result)
    return {"status": "ready"}
