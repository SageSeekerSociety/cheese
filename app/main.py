from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import (
    health,
    notifications,
    teams,
    tasks,
    users,
    projects,
    spaces,
    questions,
    discussions,
    ai,
    attachments,
    avatars,
    materials,
    comments,
    groups,
    materialbundles,
    topics_legacy,
    answers,
    knowledge,
)
from app.core.config import settings
from app.core import errors as core_errors
from app.core.logging import setup_logging
from app.db.session import AsyncSessionLocal
from app.domain.notification.scheduler import NotificationAggregationFinalizer
from app.middleware.tracing import TracingMiddleware
from app.auth.domains import (
    register_team_permissions,
    register_task_permissions,
    register_space_permissions,
    register_knowledge_permissions,
    register_question_permissions,
)

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

    finalizer = NotificationAggregationFinalizer(
        session_factory=AsyncSessionLocal,
        interval_seconds=settings.notification_aggregation_finalize_interval_seconds,
    )

    @app.on_event("startup")
    async def _start_notification_jobs() -> None:
        await finalizer.start()

    @app.on_event("shutdown")
    async def _stop_notification_jobs() -> None:
        await finalizer.stop()

    app.state.notification_aggregation_finalizer = finalizer

    return app


app = create_app()
