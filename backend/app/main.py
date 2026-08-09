"""CheeseX backend — FastAPI application.

Phase 0 goal (spec §13): in one topic you can talk with 芝士, and 芝士 answers
with memory. Subsequent phases (collaboration, docs, topic tree, institution
layer) build on the same data model.

Routers are auto-discovered from ``app/api/routes/`` — every module that defines
a top-level ``router`` is included. This lets domains be added without editing
this file.
"""

# dogfood loop: accepted on cheesex, deployed to dev (2026-07-18)

import importlib
import logging
import pkgutil
import re

# (logging is configured right after imports — see basicConfig below.)
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.api.routes as routes_pkg
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.obs import bind_context, clear_context, configure_logging, get_logger
from app.core.sandbox_auth import is_valid_cheese_token
from app.core.turn_context import current_turn_id, parse_turn_id
from app.core.ws_diagnostics import LogRefusedWebSockets

# Observable (可观测性军规): structlog + contextvars — every line timestamped,
# every request/turn correlated. See app/core/obs.py.
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema is managed by Alembic migrations. Start the deterministic scheduler
    # loop (定期巡检 / lifecycle, spec §9.1) — no-op unless the interval is set.
    # Per-topic sandbox containers are long-lived and REUSED across backend
    # restarts: their mounts are stable host paths (worktree + session dirs), so
    # a redeploy must NOT reap them — that killed in-flight work and raced the
    # first turns after a restart. The claude-sbx shim validates each container
    # against the project's current image and recreates it only when the image
    # changed. (reap_sandbox_containers stays available as an ops tool.)
    # Orphan sweep: resume turns the previous process died with (see
    # TurnRunner.resume_orphans) — a deploy must never silently eat a turn.
    from app.api.deps import get_chat_service, get_turn_runner
    from app.domain.scheduler.service import (
        SandboxReaperRunner,
        SchedulerRunner,
        SchedulerService,
    )

    # agent-as-user (fusion-design §2): guarantee 芝士 exists as a real user with
    # its platform agent-binding. Idempotent — the migration seeds it too; this is
    # the belt-and-suspenders path for a fresh DB or a redeploy. Never blocks boot.
    try:
        from app.core.db import async_session_factory
        from app.domain.identity.services import IdentityService

        async with async_session_factory() as session:
            await IdentityService(session).ensure_agent_user()
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — a missing table (pre-migration) must not crash boot
        get_logger("cheesex.runtime").warning(
            "agent-user seed skipped", reason=str(exc)[:120]
        )

    try:
        n = await get_turn_runner().resume_orphans(get_chat_service())
        if n:
            get_logger("cheesex.runtime").info("orphan_sweep", resumed=n)
    except Exception:  # noqa: BLE001 — never block startup
        get_logger("cheesex.runtime").exception("orphan sweep failed")

    scheduler = SchedulerService(chat_service=get_chat_service())
    runner = SchedulerRunner(scheduler, settings.scheduler_interval_seconds)
    runner.start()
    reaper = SandboxReaperRunner(
        scheduler,
        settings.sandbox_reap_interval_seconds,
        settings.sandbox_idle_hours,
    )
    reaper.start()

    # Enrolling provisioned machines is platform plumbing, so it runs on its own
    # interval rather than the AI scheduler's — see MachineEnrollmentRunner.
    from app.core.db import async_session_factory
    from app.domain.machine.runner import MachineEnrollmentRunner

    machines = MachineEnrollmentRunner(
        async_session_factory, settings.machine_enroll_interval_seconds
    )
    machines.start()
    try:
        yield
    finally:
        await machines.stop()
        await reaper.stop()
        await runner.stop()


# Route modules that failed to import this boot. Read by /healthz so a partially
# mounted app cannot pass a health check quietly.
FAILED_ROUTE_MODULES: list[str] = []


def _discover_routers(application: FastAPI) -> list[str]:
    """Include every ``APIRouter`` defined at module level in any route module.
    Resilient: a module that fails to import is skipped rather than breaking the
    whole app. A module may export more than one router."""
    loaded: list[str] = []
    seen: set[int] = set()
    FAILED_ROUTE_MODULES.clear()
    for module_info in pkgutil.iter_modules(routes_pkg.__path__):
        name = f"{routes_pkg.__name__}.{module_info.name}"
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover - guards parallel/dev breakage
            # LOUD on purpose: a silently skipped module drops its whole router,
            # which once removed /sandbox/hooks (every agent event 404'd) because
            # an unrelated module-level file read failed in the image.
            logging.getLogger("app.startup").exception(
                "route module %s failed to import — its routes are NOT mounted", name
            )
            FAILED_ROUTE_MODULES.append(name)
            # Outside production, refuse to start. A skipped module leaves the
            # service reporting healthy while a whole group of endpoints answers
            # 404, and the only symptom reaches the CALLER — so a typo can ship.
            # Production keeps the resilience (one bad module must not take the
            # whole app down) and surfaces the damage through /healthz instead.
            if settings.environment != "production":
                raise
            continue
        for attr, value in vars(module).items():
            if isinstance(value, APIRouter) and id(value) not in seen:
                application.include_router(value)
                seen.add(id(value))
                loaded.append(f"{module_info.name}.{attr}")
    return loaded


app = FastAPI(title="CheeseX", version="0.1.0", lifespan=lifespan)

app.add_middleware(LogRefusedWebSockets)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)

# Wire the domain permission configs + role providers into the shared checker.
# Without this every require_permission()-gated endpoint 403s (the providers
# would otherwise be empty). Idempotent-enough for a single process start.
from app.auth.domains import register_all_permissions  # noqa: E402

register_all_permissions()


# The `cheese` CLI (running inside the sandbox container) reaches the backend
# over the network, so its write-surface must not be open like the browser API.
# These paths are cheese-only writes (the frontend only reads them); the gate
# verifies a per-turn token scoped to the URL's project/topic (review R5).
# doc/split/title are dual-use (the doc panel saves, the sidebar splits and
# renames) so they stay open like the rest of the app, protected by
# ActorResolverDep + authorize_topic instead — closing those needs browser
# user-auth first.
# Each pattern captures the scoping id as group "topic" or "project".
_CHEESE_WRITE_PATHS: list[tuple[str, re.Pattern[str]]] = [
    ("POST", re.compile(r"^/api/topics/(?P<topic>[^/]+)/webhook-token$")),
    ("POST", re.compile(r"^/api/topics/(?P<topic>[^/]+)/decision$")),
    ("POST", re.compile(r"^/api/topics/(?P<topic>[^/]+)/return-conclusion$")),
    ("POST", re.compile(r"^/api/topics/(?P<topic>[^/]+)/accept-card$")),
    ("POST", re.compile(r"^/api/projects/(?P<project>[^/]+)/memory$")),
    ("POST", re.compile(r"^/api/projects/(?P<project>[^/]+)/notifications$")),
    ("POST", re.compile(r"^/api/projects/(?P<project>[^/]+)/milestones$")),
]


_http_log = get_logger("http")


@app.middleware("http")
async def request_context(request: Request, call_next: Callable):  # type: ignore[type-arg]
    """Correlation + timing for every request: bind request_id (respecting an
    incoming X-Request-ID) to the async context, echo it back, log the duration.
    contextvars are task-local, so concurrent requests never bleed ids."""
    import time
    import uuid as _uuid

    rid = request.headers.get("x-request-id") or _uuid.uuid4().hex[:12]
    bind_context(req=rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        _http_log.exception(
            "request failed", method=request.method, path=request.url.path
        )
        raise
    finally:
        clear_context("req")
    ms = round((time.perf_counter() - t0) * 1000, 1)
    # WS upgrades and health probes are logged by their own layers; skip noise.
    if request.url.path != "/health":
        _http_log.info(
            "req",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=ms,
            req=rid,
        )
    response.headers["X-Request-ID"] = rid
    return response


@app.middleware("http")
async def cheese_token_gate(request: Request, call_next: Callable):  # type: ignore[type-arg]
    method, path = request.method, request.url.path
    for m, rx in _CHEESE_WRITE_PATHS:
        if method != m:
            continue
        match = rx.match(path)
        if match is None:
            continue
        ids = match.groupdict()
        token = request.headers.get("x-cheese-token") or ""
        if not is_valid_cheese_token(
            token, project_id=ids.get("project"), topic_id=ids.get("topic")
        ):
            return JSONResponse(
                {"code": 401, "message": "invalid sandbox token", "data": None},
                status_code=401,
            )
        break
    # Stash the cheese turn id so blocks written by this request inherit it (R4).
    ctx = current_turn_id.set(parse_turn_id(request.headers.get("x-cheese-turn")))
    try:
        return await call_next(request)
    finally:
        current_turn_id.reset(ctx)


loaded_routers = _discover_routers(app)


@app.get("/debug/turns")
async def debug_turns() -> dict:
    """可 debug: the last ~100 turns' lifecycle summaries (status, timings,
    tool counts, failure reasons) — read the state of the world without
    grepping logs."""
    from app.api.deps import get_turn_runner

    return {"code": 200, "message": "ok", "data": get_turn_runner().recent_turns()}


@app.get("/health")
async def health() -> dict:
    from app.api.deps import get_turn_runner

    # active_turns lets a redeploy drain: wait until no agent turn is in flight
    # before restarting, so a deploy never kills 芝士 mid-work.
    return {
        "code": 200,
        "message": "ok",
        "data": {"status": "healthy", "active_turns": get_turn_runner().active_turns()},
    }
