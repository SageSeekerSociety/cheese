"""What this process reports about itself, and what decides whether it is ready.

Three callers ask, none of them behind a request that carries a credential: the
``/readyz`` and ``/health/detailed`` routes, and the platform dashboard's health
snapshot (``app.domain.platform_stats.services``). The first two are HTTP and
live in ``app/api/routes/health.py``; the third is a domain service with no
request at all. The judging sits here, below both, so the answer is computed in
one place and read downward from wherever it is asked for.

**Reported is not required.** ``_REQUIRED_CHECKS`` is the only thing that can
make ``/readyz`` answer 503. A degraded advisory check still shows up in the
detailed report and in the dashboard — which is where a human or an alert looks
— but taking a process out of rotation over one degraded feature would trade it
for a total outage.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from redis.asyncio import Redis as AsyncRedis
from sqlalchemy import text

from app.core import alerting
from app.core.config import settings
from app.core.db import PROBE_TIMEOUT_S
from app.core.loop_lag import STALL_S, lag_status
from app.core.route_mounting import FAILED_ROUTE_MODULES

logger = logging.getLogger(__name__)

# Which entries of `checks` decide whether this process should take traffic.
# Everything else in there is reported for a human and for alerting, which is a
# different question: a process that can still answer every request is not
# unready because one feature is degraded. Keeping the two apart is what lets a
# check be loud without also being a switch that pulls the whole platform out
# of rotation — see `event_loop`.
_REQUIRED_CHECKS = ("database", "redis", "routes")

# A check that had nothing to do is not a failing check.
_HEALTHY_STATUSES = frozenset({"up", "skipped"})

# Installed and cleared by the application lifespan; core never imports its
# composition root to inspect the clocks that process owns.
_periodic_health: Callable[[], dict[str, Any]] | None = None


def set_periodic_health(read: Callable[[], dict[str, Any]] | None) -> None:
    global _periodic_health
    _periodic_health = read


# Checks a domain answers — whether a product loop the platform ships is
# actually wired up on this deployment — installed by the application lifespan
# for the same reason as `_periodic_health`: core never imports a domain. Each
# answers `{"status": ..., "error": "<why>"}`, reads the database, and so is
# never run on behalf of the public `/readyz` (`health_report(domain=False)`).
_domain_checks: dict[str, Callable[[], Awaitable[dict[str, Any]]]] = {}


def set_domain_checks(
    checks: dict[str, Callable[[], Awaitable[dict[str, Any]]]],
) -> None:
    _domain_checks.clear()
    _domain_checks.update(checks)


async def _run_domain_check(
    check: Callable[[], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    try:
        return await asyncio.wait_for(check(), PROBE_BUDGET_S)
    except TimeoutError:
        return {"status": "down", "error": f"no answer in {PROBE_BUDGET_S:g} s"}
    except Exception as e:  # a broken check reads as down, never as a 500
        logger.warning("domain health check failed: %s", e)
        return {"status": "down", "error": str(e) or type(e).__name__}


async def health_report(*, domain: bool = True) -> dict[str, Any]:
    """Every check's status, in this process, with no caller behind it.

    `/readyz` reads it to decide readiness, and the admin dashboard's platform
    panel calls it in-process. Neither has a credential to present, so the gate
    sits on the route rather than here. `/readyz` passes `domain=False`: it is
    public, and the domain checks read the database on every call.
    """
    checks: dict[str, Any] = {}

    checks["database"], checks["redis"] = await dependency_probe()
    checks["routes"] = _check_routes()
    checks["event_loop"] = _check_event_loop()
    checks["alerting"] = _check_alerting()
    checks["periodic_jobs"] = (
        _periodic_health() if _periodic_health is not None else {"status": "skipped"}
    )
    if domain:
        for name, check in list(_domain_checks.items()):
            checks[name] = await _run_domain_check(check)

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

    The list is written by the composition root while mounting
    (`app/core/route_mounting.py`), so nothing here reaches up to read it.
    """
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
    # deferred-import: tests patch probe_engine on app.core.db
    from app.core.db import pool_status, probe_engine

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
