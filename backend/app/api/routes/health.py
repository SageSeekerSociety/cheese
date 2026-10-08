import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text

from app.api.routes.admin_common import PlatformAdminDep
from app.core import alerting
from app.core.config import settings
from app.core.db import PROBE_TIMEOUT_S, pool_status, probe_engine
from app.core.loop_lag import STALL_S, lag_status
from app.core.metrics import registry

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
    `/health`); while something required is down, `/readyz`'s 503 names which
    checks failed and each one's status, so an outage stays diagnosable with no
    session — the error text itself is only here.
    """
    return await health_report()


async def health_report() -> dict[str, Any]:
    """Every check's status, in this process, with no caller behind it.

    `/readyz` reads it to decide readiness, and the admin dashboard's platform
    panel calls it in-process. Neither has a credential to present, so the gate
    sits on the route above rather than here.
    """
    checks: dict[str, Any] = {}

    checks["database"], checks["redis"] = await dependency_probe()
    checks["routes"] = _check_routes()
    checks["event_loop"] = _check_event_loop()
    checks["alerting"] = _check_alerting()

    overall = (
        "healthy"
        if all(c.get("status") in _HEALTHY_STATUSES for c in checks.values())
        else "degraded"
    )
    return {"status": overall, "checks": checks}


#: How long one dependency probe's answer is reused. Every caller inside the
#: window — the container healthcheck, the rollout's curl, a stranger hammering
#: the public route — reads the same answer, so the probes this process opens
#: are bounded by time, never by how often it is asked.
PROBE_REUSE_S = 1.0

#: The most one dependency probe may take, connect and query together. The
#: driver timeouts (PROBE_TIMEOUT_S) bound each step, not their sum: connect
#: then query is up to twice that, past the rollout's 3 s curl.
PROBE_BUDGET_S = 2.5


class _SharedProbe:
    """The database and Redis probes, run once at a time for every caller.

    `/readyz` is public, and each probe opens a fresh connection. Probed per
    request, a few hundred concurrent anonymous requests would hold a few
    hundred Postgres connections and leave the app's own pool none. Here a
    caller that arrives while a probe is running waits on that probe, and one
    that arrives within PROBE_REUSE_S of an answer reads it: one connection per
    process at a time, whatever the request rate.

    The probe runs as a task of its own, shielded from its callers: a caller
    that hangs up must not cancel the answer everyone else is waiting on.
    """

    def __init__(self) -> None:
        self._running: asyncio.Task[tuple[dict[str, Any], dict[str, Any]]] | None = None
        self._answer: tuple[dict[str, Any], dict[str, Any]] | None = None
        self._answered_at = 0.0

    def forget(self) -> None:
        """Drop the reused answer and any probe in flight (tests: one per loop)."""
        self._running = None
        self._answer = None
        self._answered_at = 0.0

    async def __call__(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if (
            self._answer is not None
            and time.monotonic() - self._answered_at < PROBE_REUSE_S
        ):
            return self._answer
        running = self._running
        if running is None or running.get_loop() is not asyncio.get_running_loop():
            running = self._running = asyncio.create_task(self._probe())
        return await asyncio.shield(running)

    async def _probe(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            # Side by side, so the two budgets do not add up.
            database, redis = await asyncio.gather(
                _within_budget(_check_database), _within_budget(_check_redis)
            )
            # Only the current probe answers: after `forget` this one is stale.
            if self._running is asyncio.current_task():
                self._answer = (database, redis)
                self._answered_at = time.monotonic()
            return database, redis
        finally:
            if self._running is asyncio.current_task():
                self._running = None


async def _within_budget(
    check: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """A probe's answer, or "down" once PROBE_BUDGET_S has passed without one."""
    try:
        return await asyncio.wait_for(check(), PROBE_BUDGET_S)
    except TimeoutError:
        logger.warning("%s gave no answer in %ss", check.__name__, PROBE_BUDGET_S)
        return {"status": "down", "error": f"no answer in {PROBE_BUDGET_S:g} s"}


dependency_probe = _SharedProbe()


def _check_routes() -> dict[str, Any]:
    """Whether every route module mounted this boot.

    Production skips a module that fails to import so one bad file cannot take
    the app down — but the app then answers 404 for that module's whole group
    of endpoints, and only the caller finds out. Required, so `/readyz` turns
    it into a 503 and the rollout keeps such a build away from traffic.
    """
    # deferred-import: breaks the cycle app.main -> route modules -> health
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

    lag = lag_status()
    return {
        "status": "up" if lag["recent_ms"] < STALL_S * 1000 else "stalling",
        **lag,
    }


def _check_alerting() -> dict[str, Any]:
    """Whether this deployment can reach a human when something breaks.

    Reported, never required: a box whose webhook is unset still answers every
    request, and failing readiness over a missing setting would trade an
    invisible gap for a real outage.

    Outside production an unset webhook is the normal state — dev and test do
    not open Feishu — so it reads `skipped`. In production it reads `down`,
    because that is the one deployment where "no alerts" means nobody ever
    hears about an outage. Nothing else looks: `alerting.send` is a no-op when
    the webhook is unset, and neither this report nor the boot path said so, so
    the whole channel could be off while every page read healthy.
    """
    if alerting.configured():
        return {"status": "up"}
    if settings.environment != "production":
        return {"status": "skipped"}
    return {
        "status": "down",
        "error": "FEISHU_ALERT_WEBHOOK is unset: alerts reach nobody",
    }


async def _check_database() -> dict[str, Any]:

    try:
        async with probe_engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return {"status": "up", "pool": pool_status()}
    except Exception as e:
        logger.warning("Database health check failed: %s", e)
        return {"status": "down", "error": str(e) or type(e).__name__}


async def _check_redis() -> dict[str, Any]:
    try:
        # Bounded like the database probe: an unreachable Redis that drops
        # packets would otherwise hold each probe for the TCP timeout.
        redis = AsyncRedis.from_url(
            settings.redis_url,
            socket_connect_timeout=PROBE_TIMEOUT_S,
            socket_timeout=PROBE_TIMEOUT_S,
        )
        try:
            await redis.ping()
            return {"status": "up"}
        finally:
            await redis.aclose()
    except Exception as e:
        logger.warning("Redis health check failed: %s", e)
        return {"status": "down", "error": str(e) or type(e).__name__}


@router.get("/metrics", summary="Application metrics")
async def get_metrics(_admin: PlatformAdminDep) -> dict[str, Any]:
    """The in-process metric registry, for a platform admin.

    Every route's call count, latency and error count lives in here, which is
    a map of the platform's insides — not something the open internet reads.
    No scraper is configured to read it today, so nothing external breaks when
    it goes behind the gate.
    """

    return registry.export()


@router.get("/readyz", summary="Readiness check")
async def readiness_check() -> Any:
    """Ready means "can serve requests", which is narrower than "all green".

    Only `_REQUIRED_CHECKS` can make this 503. A degraded advisory check still
    shows up in `/health/detailed` — that is where a human or an alert looks —
    but taking the process out of rotation over it would trade one degraded
    feature for a total outage.

    The 503 is a plain JSON response, not an `HTTPException`: the app's error
    envelope would replace the body with a generic "HTTP error". The body names
    which required checks failed and each check's status, plus the modules that
    did not mount — enough for a rollout log to say why. The error text of a
    failing dependency stays behind `/health/detailed`: this route is public,
    and that text can carry internal hosts and users.
    """
    result = await health_report()
    checks = result["checks"]
    unready = [
        name for name in _REQUIRED_CHECKS if checks.get(name, {}).get("status") != "up"
    ]
    if unready:
        public = {name: {"status": body.get("status")} for name, body in checks.items()}
        if "unmounted" in checks.get("routes", {}):
            public["routes"]["unmounted"] = checks["routes"]["unmounted"]
        return JSONResponse(
            status_code=503,
            content={"status": "unready", "unready": unready, "checks": public},
        )
    return {"status": "ready"}
