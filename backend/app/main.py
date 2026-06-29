"""CheeseX backend — FastAPI application.

Phase 0 goal (spec §13): in one topic you can talk with 芝士, and 芝士 answers
with memory. Subsequent phases (collaboration, docs, topic tree, institution
layer) build on the same data model.

Routers are auto-discovered from ``app/api/routes/`` — every module that defines
a top-level ``router`` is included. This lets domains be added without editing
this file.
"""

import importlib
import pkgutil
import re
import secrets
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import app.api.routes as routes_pkg
from app.core.config import settings
from app.core.errors import register_exception_handlers
from app.core.sandbox_auth import SANDBOX_TOKEN


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema is managed by Alembic migrations. Start the deterministic scheduler
    # loop (定期巡检 / lifecycle, spec §9.1) — no-op unless the interval is set.
    from app.api.deps import get_chat_service
    from app.domain.scheduler.service import SchedulerRunner, SchedulerService

    # Per-topic sandbox containers are long-lived but their mounts are tied to a
    # specific process's worktree paths; drop any left over from a previous run so
    # each topic recreates a fresh one on its next turn.
    if settings.agent_sandbox_enabled:
        from app.domain.workspace import service as ws

        reaped = ws.reap_sandbox_containers()
        if reaped:
            print(f"[sandbox] reaped {reaped} stale container(s) at startup")

    scheduler = SchedulerService(chat_service=get_chat_service())
    runner = SchedulerRunner(scheduler, settings.scheduler_interval_seconds)
    runner.start()
    try:
        yield
    finally:
        await runner.stop()


def _discover_routers(application: FastAPI) -> list[str]:
    """Include every ``APIRouter`` defined at module level in any route module.
    Resilient: a module that fails to import is skipped rather than breaking the
    whole app. A module may export more than one router."""
    loaded: list[str] = []
    seen: set[int] = set()
    for module_info in pkgutil.iter_modules(routes_pkg.__path__):
        name = f"{routes_pkg.__name__}.{module_info.name}"
        try:
            module = importlib.import_module(name)
        except Exception:  # pragma: no cover - guards parallel/dev breakage
            continue
        for attr, value in vars(module).items():
            if isinstance(value, APIRouter) and id(value) not in seen:
                application.include_router(value)
                seen.add(id(value))
                loaded.append(f"{module_info.name}.{attr}")
    return loaded


app = FastAPI(title="CheeseX", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

register_exception_handlers(app)


# The `cheese` CLI (running inside the sandbox container) reaches the backend
# over the network, so its write-surface must not be open like the browser API.
# These paths are cheese-only writes (the frontend only reads them); gate them
# on the shared token. See app/core/sandbox_auth.py.
# Only paths that ONLY the cheese CLI writes. doc/split are dual-use (the doc
# panel saves, the sidebar splits), so they stay open like the rest of the app.
_CHEESE_WRITE_PATHS: list[tuple[str, re.Pattern[str]]] = [
    ("POST", re.compile(r"^/api/topics/[^/]+/decision$")),
    ("POST", re.compile(r"^/api/topics/[^/]+/return-conclusion$")),
    ("POST", re.compile(r"^/api/topics/[^/]+/accept-card$")),
    ("POST", re.compile(r"^/api/projects/[^/]+/memory$")),
    ("POST", re.compile(r"^/api/projects/[^/]+/notifications$")),
    ("POST", re.compile(r"^/api/projects/[^/]+/milestones$")),
]


@app.middleware("http")
async def cheese_token_gate(request: Request, call_next: Callable):  # type: ignore[type-arg]
    method, path = request.method, request.url.path
    if any(method == m and rx.match(path) for m, rx in _CHEESE_WRITE_PATHS):
        token = request.headers.get("x-cheese-token") or ""
        if not secrets.compare_digest(token, SANDBOX_TOKEN):
            return JSONResponse(
                {"code": 401, "message": "invalid sandbox token", "data": None},
                status_code=401,
            )
    return await call_next(request)


loaded_routers = _discover_routers(app)


@app.get("/health")
async def health() -> dict:
    return {"code": 200, "message": "ok", "data": {"status": "healthy"}}
