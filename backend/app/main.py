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
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.api.routes as routes_pkg
from app.core.config import settings
from app.core.errors import register_exception_handlers


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Schema is managed by Alembic migrations. Start the deterministic scheduler
    # loop (定期巡检 / lifecycle, spec §9.1) — no-op unless the interval is set.
    from app.api.deps import get_chat_service
    from app.domain.scheduler.service import SchedulerRunner, SchedulerService

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
loaded_routers = _discover_routers(app)


@app.get("/health")
async def health() -> dict:
    return {"code": 200, "message": "ok", "data": {"status": "healthy"}}
