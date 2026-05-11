import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import (
    ai,
    answers,
    attachments,
    avatars,
    comments,
    discussions,
    groups,
    health,
    knowledge,
    materialbundles,
    materials,
    notifications,
    projects,
    questions,
    recruitment,
    spaces,
    tasks,
    teams,
    topics_legacy,
    users,
)
from app.auth.domains import (
    register_knowledge_permissions,
    register_question_permissions,
    register_space_permissions,
    register_task_permissions,
    register_team_permissions,
)
from app.core import errors as core_errors
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.domain.notification.scheduler import NotificationAggregationFinalizer
from app.middleware.tracing import TracingMiddleware

setup_logging()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Cheese Backend (Python)",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # Middleware
    app.add_middleware(TracingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=settings.cors_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Authorization", "Content-Disposition"],
        max_age=3600,
    )

    # Global error handlers（对齐 Kotlin BaseError / GlobalErrorHandler 结构）
    app.add_exception_handler(core_errors.BaseError, core_errors.base_error_handler)
    app.add_exception_handler(StarletteHTTPException, core_errors.http_exception_handler)
    app.add_exception_handler(RequestValidationError, core_errors.validation_exception_handler)

    # Register domain permissions
    register_team_permissions()
    register_task_permissions()
    register_space_permissions()
    register_knowledge_permissions()
    register_question_permissions()

    # Routers
    app.include_router(health.router)
    app.include_router(notifications.router)
    app.include_router(teams.router)
    app.include_router(tasks.router)
    app.include_router(users.router)
    app.include_router(projects.router)
    app.include_router(spaces.router)
    app.include_router(questions.router)
    app.include_router(discussions.router)
    app.include_router(ai.router)
    app.include_router(attachments.router)
    app.include_router(avatars.router)
    app.include_router(materials.router)
    app.include_router(comments.router)
    app.include_router(groups.router)
    app.include_router(materialbundles.router)
    app.include_router(topics_legacy.router)
    app.include_router(answers.router)
    app.include_router(knowledge.router)
    app.include_router(recruitment.router)
    app.include_router(recruitment.team_recruitment_router)

    # Mount uploads directory for serving images and other static files
    import os

    uploads_path = os.path.abspath(settings.storage_local_path)
    if not os.path.isdir(uploads_path):
        os.makedirs(uploads_path, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=uploads_path), name="uploads")

    finalizer = NotificationAggregationFinalizer(
        session_factory=AsyncSessionLocal,
        interval_seconds=settings.notification_aggregation_finalize_interval_seconds,
    )

    @app.on_event("startup")
    async def _start_notification_jobs() -> None:
        await finalizer.start()

    @app.on_event("startup")
    async def _setup_search_indices() -> None:
        from app.domain.search.meilisearch_service import setup_indices

        try:
            setup_indices()
        except Exception:
            import logging

            logging.getLogger(__name__).warning(
                "Meilisearch index setup failed — search will use PG FTS fallback",
                exc_info=True,
            )

    @app.on_event("shutdown")
    async def _stop_notification_jobs() -> None:
        await finalizer.stop()

    app.state.notification_aggregation_finalizer = finalizer

    return app


app = create_app()
