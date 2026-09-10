"""CheeseX backend — FastAPI application.

Phase 0 goal (spec §13): in one topic you can talk with 芝士, and 芝士 answers
with memory. Subsequent phases (collaboration, docs, topic tree, institution
layer) build on the same data model.

Routers are auto-discovered from ``app/api/routes/`` — every module that defines
a top-level ``router`` is included. This lets domains be added without editing
this file.
"""

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
from app.api.auth import ActorResolver
from app.core.config import settings
from app.core.db import get_db
from app.core.errors import BaseError, register_exception_handlers
from app.core.obs import (
    ResponseIntegrityAudit,
    bind_context,
    clear_context,
    configure_logging,
    get_logger,
)
from app.core.sandbox_auth import (
    is_global_sandbox_token,
    is_valid_cheese_token,
    looks_like_project_agent_credential,
    scoped_token_claims,
)
from app.core.work_context import current_work_id, parse_work_id
from app.core.ws_diagnostics import LogRefusedWebSockets
from app.domain import backend_log  # module import: tests swap the intake singleton
from app.domain.agent_credential.services import ProjectAgentCredentialService

# Observable (可观测性军规): structlog + contextvars — every line timestamped,
# every request/turn correlated. See app/core/obs.py.
configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema is managed by Alembic migrations. Start the deterministic scheduler
    # loop (定期巡检 / lifecycle, spec §9.1) — no-op unless the interval is set.
    # Orphan sweep: resume turns the previous process died with (see
    # AgentWorkRunner.resume_orphans) — a deploy must never silently eat a turn.
    # A screen outlives this process, which is exactly why the hook credential
    # must survive a restart: its token is baked into the environment of the
    # long-running `claude` at launch and never refreshed. An unpinned
    # SANDBOX_TOKEN used to mean a fresh random secret per PROCESS, so every
    # restart silently invalidated every live screen at once — that is fixed at
    # the root now (`Settings.sandbox_signing_secret` derives a stable secret
    # from jwt_secret), and there is nothing left to warn about here.

    from app.api.deps import get_chat_service, get_work_runner
    from app.domain.scheduler.service import SchedulerService

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

    # The backend and the in-container agent share one git store and must run as
    # the same uid (ws.AGENT_UID). When they don't, nothing here fails — the file
    # panel just 422s for every topic in the project. Say it out loud at boot.
    try:
        from app.domain.workspace import service as _ws

        for problem in _ws.audit_workspace_ownership():
            get_logger("cheesex.runtime").error(
                "workspace_ownership", problem=problem, uid=_ws.AGENT_UID
            )
    except Exception:  # noqa: BLE001 — a diagnostic must never block boot
        get_logger("cheesex.runtime").exception("workspace ownership audit failed")

    # The `cheese` CLI is now staged into each topic's session dir from THIS
    # build (ws.session_dir) instead of an operator-maintained host checkout. A
    # box still setting the retired var is the exact configuration that served a
    # months-old CLI to every agent, so say so instead of ignoring it silently.
    if settings.sandbox_shim_host_dir.strip():
        get_logger("cheesex.runtime").warning(
            "sandbox_shim_host_dir_retired",
            value=settings.sandbox_shim_host_dir.strip(),
            detail=(
                "SANDBOX_SHIM_HOST_DIR is no longer used to mount the cheese CLI "
                "(it is staged per-topic from this backend build). Remove it from "
                "the box .env — a checkout there is no longer kept in sync."
            ),
        )

    # #370 step 2 flattened the platform routes, so the in-container `cheese`
    # CLI's base is now the app root. A box still carrying the old `…/api` value
    # keeps working — `settings.agent_api_base()` strips it — but say so, because
    # the failure it would otherwise cause is invisible: every platform action
    # 404s and the turn just looks like an agent that chose not to use its tools.
    if settings.sandbox_api_base.rstrip("/").endswith("/api"):
        get_logger("cheesex.runtime").warning(
            "sandbox_api_base_has_stale_api_suffix",
            value=settings.sandbox_api_base,
            detail=(
                "SANDBOX_API_BASE still ends in /api. The platform routes no "
                "longer carry that prefix, so the value is being normalised to "
                "the app root. Drop the /api from the box .env."
            ),
        )

    try:
        recovered = await get_chat_service().recover_sessions()
        if recovered:
            get_logger("cheesex.runtime").info(
                "hook_subscriptions_recovered", topics=recovered
            )
    except Exception:  # noqa: BLE001 — never block startup
        get_logger("cheesex.runtime").exception("hook subscription recovery failed")

    try:
        n = await get_work_runner().resume_orphans(get_chat_service())
        if n:
            get_logger("cheesex.runtime").info("orphan_sweep", resumed=n)
    except Exception:  # noqa: BLE001 — never block startup
        get_logger("cheesex.runtime").exception("orphan sweep failed")

    scheduler = SchedulerService(chat_service=get_chat_service())

    # 闸门孤儿卡扫底 (2026-08-11): the gate runner is an in-memory asyncio task,
    # so a redeploy kills every check in flight and nobody ever calls
    # finish_gate — the card sits in `pending_gate` forever AND blocks its topic
    # from ever filing another card (create_card's mutex). This runs BEFORE the
    # periodic loop starts, and does the whole point of the startup path: right
    # now `gate.in_flight_card_ids()` is empty, so everything past the deadline
    # is provably abandoned by the process that died, not by this one.
    try:
        swept = await scheduler.sweep_abandoned_gates()
        if swept["condemned"] or swept["errors"]:
            get_logger("cheesex.runtime").info(
                "gate_sweep_startup",
                condemned=len(swept["condemned"]),
                errors=swept["errors"],
            )
    except Exception:  # noqa: BLE001 — never block startup
        get_logger("cheesex.runtime").exception("startup gate sweep failed")

    from app.api.deps import get_cloud_wakeup
    from app.core.db import async_session_factory
    from app.domain.machine.runner import MachineEnrollmentSweeper
    from app.domain.scheduler.jobs import periodic_jobs

    jobs = periodic_jobs(
        scheduler=scheduler,
        machines=MachineEnrollmentSweeper(
            async_session_factory,
            on_ready=get_cloud_wakeup().wake,
            on_failed=get_cloud_wakeup().report_failures,
        ),
        sessions=async_session_factory,
    )
    for job in jobs:
        job.start()
    from app.core.background import spawn
    from app.domain.topic.retire import sweep_retired_storage

    spawn(sweep_retired_storage(async_session_factory), name="cleanup startup recovery")

    # The openviking backend's whole failure mode is silence: a rejected key
    # leaves extraction writing nothing, recall answering empty, and no other
    # symptom anywhere — indistinguishable from the db backend, which also
    # never learns on its own. So somebody has to actually call the endpoints,
    # and boot is when: whoever just flipped MEMORY_BACKEND is reading this log
    # right now. No-op on the db backend, and it never raises — a model vendor
    # outage must not keep the rest of the platform from starting.
    from app.domain.memory import endpoint_probe as memory_endpoint_probe

    await memory_endpoint_probe.check_on_startup()

    from app.domain.machine.microcloud import reuse_connections

    async with reuse_connections():
        try:
            yield
        finally:
            for job in reversed(jobs):
                await job.stop()
            # The openviking backend keeps the whole memory tree in one embedded
            # instance (AGFS + vector index) under openviking_data_dir. Nothing
            # else owns its lifecycle, so a redeploy would tear the process down
            # mid-write; closing it here is what makes the data on that volume a
            # consistent thing to come back to. No-op on the db backend.
            if settings.memory_backend == "openviking":
                try:
                    from app.domain.memory.openviking_store import get_runtime

                    await get_runtime().close()
                except Exception:  # noqa: BLE001 — shutdown must still finish
                    get_logger("cheesex.runtime").exception(
                        "openviking shutdown failed"
                    )


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


# Where this app hangs off the origin a caller can actually reach. The frontend
# image's nginx owns the public origin and forwards the API with
# `location /api/ { proxy_pass http://backend:8081/; }` — the trailing slash makes
# it strip exactly this one segment — so a route's own path is never a URL anybody
# can send. Publishing it as an OpenAPI server is what makes the schema
# self-addressing: server + path is the URL, and since #370 retired the 2.0
# routers' own prefix that composition is the same one shape for every route.
#
# Left unset, the schema advertised bare backend paths, and a caller who followed
# them got no error worth the name: measured 2026-08-12, of the 135 paths under
# the 2.0 prefix, 128 answered 404 and 7 reached a DIFFERENT 1.0 route that
# answered as if the call were its own — 10 endpoints at method+path granularity.
# The exact collision set is recomputed and pinned by
# tests/contract/test_api_addressing_contract.py, so read the failure there, not
# these numbers, when the surface moves. Same convention as
# `settings.connector_public_base`, which already has to end in `/api` for the
# same reason. See docs/api-conventions.md.
API_GATEWAY_MOUNT = "/api"

app = FastAPI(
    title="CheeseX",
    version="0.1.0",
    lifespan=lifespan,
    # A trailing slash is a 404, never a redirect — because behind this gateway a
    # slash-redirect cannot be made correct. Starlette answers an unmatched
    # `/topics/42/` with a 307 whose `Location` is ORIGIN-ABSOLUTE and built from
    # the path IT was handed — the stripped one — so the browser is sent to
    # `<origin>/topics/42`, which carries no `/api` and which the gateway does
    # not route here at all.
    #
    # Setting `root_path` does not repair it: Starlette stopped putting root_path
    # back on slash redirects in 0.35.0 (starlette#2514, still open). Measured on
    # the version we run — 1.3.1, with root_path="/api" explicitly set — the
    # Location is still `http://testserver/topics/42`. So turning the behaviour
    # off is the fix and not a workaround. No route declares a trailing slash, so
    # nothing canonical changes; only the extra-slash spelling stops being
    # silently accepted.
    redirect_slashes=False,
    servers=[
        {
            "url": API_GATEWAY_MOUNT,
            "description": "Through the app origin — browsers and external callers",
        },
        {
            "url": "/",
            "description": "Straight at the backend port, with no gateway in front",
        },
    ],
)

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
# These patterns are the gate itself, and they are written as TEXT — so they do
# not follow a route that moves. #370 step 2 flattened the 2.0 prefix and every
# one of them stopped matching, which does not fail: it silently opens the
# cheese write-surface to anyone who can reach the port. The suite caught it
# (test_project_agent_credential, test_ask_options and test_memory_search all
# went from "refused" to "allowed"), which is the only
# reason to say it out loud here: a gate defined by strings has to be moved by
# hand whenever the strings it names do.
_CHEESE_WRITE_PATHS: list[tuple[str, re.Pattern[str]]] = [
    ("POST", re.compile(r"^/topics/(?P<topic>[^/]+)/webhook-token$")),
    # 留话给一条活: the scoping id is the SENDER (the room whose turn is talking);
    # the receiver is in the body and is checked against the threads that room
    # dispatched — this gate can only prove "some agent of this project", because
    # a project-scoped credential reaches every topic of it.
    ("POST", re.compile(r"^/topics/(?P<topic>[^/]+)/tell$")),
    # Task bind/title/close/readiness/delivery routes are shared by human and
    # agent executors. They authorize the room and task in the route itself;
    # adding them here would incorrectly restrict them to agent credentials.
    ("POST", re.compile(r"^/topics/(?P<topic>[^/]+)/lock$")),
    ("POST", re.compile(r"^/topics/(?P<topic>[^/]+)/unlock$")),
    ("POST", re.compile(r"^/projects/(?P<project>[^/]+)/memory$")),
    ("POST", re.compile(r"^/projects/(?P<project>[^/]+)/memory/search$")),
    # 记忆整理: the topic is the turn that is SPEAKING; which pools it may
    # reorganize is derived from it server-side (memory/dream.py::dream_pools).
    ("POST", re.compile(r"^/topics/(?P<topic>[^/]+)/memory/dream$")),
    # Notification creation is NOT here: humans post there too (Bearer), which
    # this gate cannot see. The route enforces its own credential check via
    # ActorResolver.require_verified_caller — same tokens accepted, plus Bearer.
    ("POST", re.compile(r"^/projects/(?P<project>[^/]+)/milestones$")),
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
        # Who and from where, when known. `auth_user_id` is set by
        # get_auth_user (request.state rides scope, so it survives the
        # middleware task boundary). XFF/UA are recorded verbatim, no trust
        # decisions — behind the edge proxy the peer address is useless for
        # telling two clients apart (all traffic arrives from the proxy).
        who: dict[str, object] = {}
        user_id = request.scope.get("state", {}).get("auth_user_id")
        if user_id is not None:
            who["user"] = user_id
        xff = request.headers.get("x-forwarded-for")
        if xff:
            who["client"] = xff
        ua = request.headers.get("user-agent")
        if ua:
            who["ua"] = ua
        # `bytes` = what we DECLARED (Content-Length), not what reached the
        # client — this line is emitted before the body hits the wire. Pair it
        # with ResponseIntegrityAudit's warnings to tell "we promised 269KB and
        # sent 269KB" apart from "we promised 269KB and the connection died at
        # 40KB", which is what an intermittent ERR_CONTENT_LENGTH_MISMATCH
        # needs answered.
        declared = response.headers.get("content-length")
        _http_log.info(
            "req",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            ms=ms,
            req=rid,
            **({"bytes": int(declared)} if declared and declared.isdigit() else {}),
            **who,
        )
    response.headers["X-Request-ID"] = rid
    return response


async def _credential_opens_gate(
    token: str,
    *,
    project_id: str | None,
    topic_id: str | None,
    screen_token: str = "",
) -> bool:
    """Whether the credential's participant may access this write-surface.

    Some execution endpoints rely on this gate, so the project match and the
    revocation check have to happen here — which means a database read, before
    the router and therefore before ``Depends(get_db)`` exists. Going through
    ``dependency_overrides`` instead of importing the session factory keeps ONE
    source of sessions: the test harness binds its own database by overriding
    ``get_db``, and a gate reading past that override would gate requests against
    a different database than the one they land in.
    """
    provider = app.dependency_overrides.get(get_db, get_db)
    sessions = provider()
    session = await anext(sessions)
    try:
        target = await ProjectAgentCredentialService(session).project_of_request(
            project_id=project_id, topic_id=topic_id
        )
        if target is None:
            return False
        import uuid

        claims = scoped_token_claims(token)
        origin = claims.get("t") if claims else None
        topic = uuid.UUID(topic_id or origin) if topic_id or origin else None
        resolver = ActorResolver(
            session=session, bearer=None, cheese_token=token, screen_token=screen_token
        )
        actor = await resolver.resolve(
            fallback_handle=None, project_id=target, topic_id=topic
        )
        if not actor.authenticated:
            return False
        if topic is not None:
            await resolver.authorize_topic(actor, project_id=target, topic_id=topic)
        else:
            await resolver.authorize_project(actor, project_id=target)
        return True
    except (BaseError, ValueError):
        return False
    finally:
        # Read-only: closing without draining skips the provider's commit, which
        # is what we want — the gate must not commit anything on the way past.
        await sessions.aclose()


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
        opened = is_valid_cheese_token(
            token, project_id=ids.get("project"), topic_id=ids.get("topic")
        )
        # A project agent credential can address topics in its project, so it
        # can't be matched against the URL by string compare the way a per-turn
        # token is — a topic path names its project only through the topic.
        if not is_global_sandbox_token(token) and (
            opened or looks_like_project_agent_credential(token)
        ):
            opened = await _credential_opens_gate(
                token,
                project_id=ids.get("project"),
                topic_id=ids.get("topic"),
                screen_token=request.headers.get("x-cheese-screen") or "",
            )
        if not opened:
            return JSONResponse(
                {"code": 401, "message": "invalid sandbox token", "data": None},
                status_code=401,
            )
        break
    # Stash the cheese turn id so blocks written by this request inherit it (R4).
    ctx = current_work_id.set(parse_work_id(request.headers.get("x-cheese-turn")))
    try:
        return await call_next(request)
    finally:
        current_work_id.reset(ctx)


@app.middleware("http")
async def report_unhandled_to_room(request: Request, call_next: Callable):  # type: ignore[type-arg]
    """The push half of the backend-error channel (app.domain.backend_log).

    Registered last among the user middleware, so it sees anything that escapes:
    everything a route handles deliberately — BaseError, AppError, HTTPException,
    validation — has already become a response further in, which is exactly the
    cut we want. An expected 4xx is normal flow and is not an incident; only a
    genuine unhandled exception reaches this except.

    One layer does sit outside it — ResponseIntegrityAudit, added below — and
    that changes nothing here: the audit only counts bytes and re-raises what it
    catches untouched, so every unhandled exception still arrives.

    The exception is re-raised untouched: this reports, it does not swallow.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        # A failure inside the intake endpoint itself must not report into the
        # same channel (a broken intake would amplify every other error).
        if not request.url.path.startswith("/api/backend-errors"):
            await backend_log.report_request_failure(
                exc,
                method=request.method,
                path=request.url.path,
                request_id=request.headers.get("x-request-id"),
            )
        raise


# Added LAST on purpose: Starlette builds the stack so the most recently added
# middleware is the OUTERMOST one, and this has to sit closest to the transport
# to count the bytes that actually leave the process. Inside a BaseHTTPMiddleware
# (which is what @app.middleware("http") builds) it would count the inner app's
# messages instead — which is exactly the number that already looks healthy when
# a response is truncated. It logs nothing on a healthy response — only when the
# body we declared and the body we sent differ.
app.add_middleware(ResponseIntegrityAudit)

# Content hosts must never reach platform APIs, including authentication routes.
from app.domain.site.hosting import SiteHostMiddleware  # noqa: E402

app.add_middleware(SiteHostMiddleware, platform=app)

from app.api.preview_host import PreviewHostMiddleware  # noqa: E402

app.add_middleware(PreviewHostMiddleware, platform=app)


loaded_routers = _discover_routers(app)


@app.get("/debug/turns")
async def debug_turns() -> dict:
    """可 debug: the last ~100 turns' lifecycle summaries (status, timings,
    tool counts, failure reasons) — read the state of the world without
    grepping logs."""
    from app.api.deps import get_work_runner

    return {"code": 200, "message": "ok", "data": get_work_runner().recent_work()}


@app.get("/health")
async def health() -> dict:
    from app.api.deps import get_work_runner

    # active_turns lets a redeploy drain: wait until no agent turn is in flight
    # before restarting, so a deploy never kills 芝士 mid-work. version rides
    # along so a deploy check can confirm the running build in one call.
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "status": "healthy",
            "active_turns": get_work_runner().active_work_count(),
            "version": settings.app_version,
        },
    }


@app.get("/version")
async def app_version() -> dict:
    """The running build, for the UI's 内测 version badge. Public, unauthenticated
    — it exposes only a commit sha, and only when the box opts in. `badge` is the
    flag the frontend honours; the sha is always returned so a curl can check a
    deploy regardless of the badge."""
    sha = settings.app_version
    return {
        "code": 200,
        "message": "ok",
        "data": {
            "sha": sha,
            "short": sha[:7] if sha and sha != "dev" else sha,
            "badge": settings.show_version_badge,
        },
    }
