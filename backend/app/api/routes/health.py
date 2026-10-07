import logging
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text

from app.api.routes.admin_common import PlatformAdminDep
from app.core.config import settings
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

# Which entries of `checks` decide whether this process should take traffic.
# Everything else in there is reported for a human and for alerting, which is a
# different question: a process that can still answer every request is not
# unready because one feature is degraded. Keeping the two apart is what lets a
# check be loud without also being a switch that pulls the whole platform out
# of rotation — see `event_loop`.
_REQUIRED_CHECKS = ("database", "redis", "routes")

# A check that had nothing to do is not a failing check.
_HEALTHY_STATUSES = frozenset({"up", "skipped"})


@router.get("/healthz", summary="Liveness check")
async def health_check() -> dict[str, Any]:
    """Live means the process is up and answering — nothing more.

    Whether it should take traffic is `/readyz`'s question, and every gate that
    decides that (the rollout, the container healthcheck) reads `/readyz`. This
    one stays 200 through a dependency outage or an unmounted route module: a
    restart fixes neither, so a liveness probe that failed on them would only
    add a restart loop to the outage.
    """
    return {"status": "ok"}


@router.get("/health/detailed", summary="Detailed health check")
async def detailed_health_check(_admin: PlatformAdminDep) -> dict[str, Any]:
    """Per-check detail, for a platform admin.

    Gated because it names the platform's dependencies and how each one is
    failing. The probes monitoring reads stay public (`/healthz`, `/readyz`,
    `/health`), and `/readyz` still carries this payload in its 503 body while
    something required is down — an outage stays diagnosable with no session.
    """
    return await health_report()


async def health_report() -> dict[str, Any]:
    """Every check's status, in this process, with no caller behind it.

    `/readyz` reads it to decide readiness, and the admin dashboard's platform
    panel calls it in-process. Neither has a credential to present, so the gate
    sits on the route above rather than here.
    """
    checks: dict[str, Any] = {}

    checks["database"] = await _check_database()
    checks["redis"] = await _check_redis()
    checks["routes"] = _check_routes()
    checks["event_loop"] = _check_event_loop()

    overall = (
        "healthy"
        if all(c.get("status") in _HEALTHY_STATUSES for c in checks.values())
        else "degraded"
    )
    return {"status": overall, "checks": checks}


def _check_routes() -> dict[str, Any]:
    """Whether every route module mounted this boot.

    Production skips a module that fails to import so one bad file cannot take
    the app down — but the app then answers 404 for that module's whole group
    of endpoints, and only the caller finds out. Required, so `/readyz` turns
    it into a 503 and the rollout keeps such a build away from traffic.
    """
    from app.main import FAILED_ROUTE_MODULES

    if FAILED_ROUTE_MODULES:
        unmounted = list(FAILED_ROUTE_MODULES)
        return {
            "status": "down",
            "unmounted": unmounted,
            "error": "unmounted: " + ", ".join(unmounted),
        }
    return {"status": "up"}


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


@router.get("/metrics", summary="Application metrics")
async def get_metrics(_admin: PlatformAdminDep) -> dict[str, Any]:
    """The in-process metric registry, for a platform admin.

    Every route's call count, latency and error count lives in here, which is
    a map of the platform's insides — not something the open internet reads.
    No scraper is configured to read it today, so nothing external breaks when
    it goes behind the gate.
    """
    from app.core.metrics import registry

    return registry.export()


@router.get("/readyz", summary="Readiness check")
async def readiness_check() -> Any:
    """Ready means "can serve requests", which is narrower than "all green".

    Only `_REQUIRED_CHECKS` can make this 503. A degraded advisory check still
    shows up in `/health/detailed` — that is where a human or an alert looks —
    but taking the process out of rotation over it would trade one degraded
    feature for a total outage.

    The 503 is a plain JSON response, not an `HTTPException`: the app's error
    envelope would replace the report with a generic "HTTP error", and the
    report is what makes an outage diagnosable with no session.
    """
    result = await health_report()
    unready = [
        name
        for name in _REQUIRED_CHECKS
        if result["checks"].get(name, {}).get("status") != "up"
    ]
    if unready:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unready",
                "unready": unready,
                "checks": result["checks"],
            },
        )
    return {"status": "ready"}
