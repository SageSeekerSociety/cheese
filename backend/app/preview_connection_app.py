"""One independently released preview owner. Start with --factory, one worker.

Does not load app.main, router discovery, work runners, or scheduled jobs.
Ingress enablement and deployment are deliberately separate from this role.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI

import app.models  # noqa: F401 — content access uses the complete mapper registry
from app.api.preview_host import PreviewHostMiddleware
from app.api.preview_owner_internal import inspector_router
from app.api.routes.app_preview import tunnel_router
from app.core.config import settings
from app.core.db import engine
from app.core.errors import register_exception_handlers
from app.core.obs import configure_logging
from app.domain.agent.preview_hub import PreviewHub


def create_app() -> FastAPI:
    configure_logging()
    hub = PreviewHub()
    incarnation = uuid.uuid4().hex

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            try:
                async with asyncio.timeout(5):
                    await hub.shutdown()
            finally:
                await engine.dispose()

    application = FastAPI(
        title="Cheese preview connection owner",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.preview_hub = hub
    application.state.preview_incarnation = incarnation
    application.include_router(tunnel_router)
    application.include_router(tunnel_router, prefix="/api")
    application.include_router(inspector_router(hub, incarnation))
    register_exception_handlers(application)
    application.add_middleware(PreviewHostMiddleware, platform=application)

    @application.get("/healthz")
    async def healthz() -> dict:
        return {
            "ok": hub.accepting,
            "build": settings.app_version,
            "role": "preview-connection-owner",
            "protocol": 1,
            "owner_incarnation": incarnation,
        }

    return application
