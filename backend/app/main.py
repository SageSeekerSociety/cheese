from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import (
    agent_tools,
    ai,
    answers,
    attachments,
    avatars,
    blocks,
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
    threads,
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
    # Expose API docs / OpenAPI schema only in development & test. In other
    # environments openapi_url=None also disables /docs and /redoc (both depend
    # on the schema), avoiding leaking the API surface in production.
    docs_enabled = settings.environment in ("development", "test")
    app = FastAPI(
        title="Cheese Backend (Python)",
        version="0.1.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
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
    app.add_exception_handler(core_errors.BaseError, core_errors.base_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, core_errors.http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, core_errors.validation_exception_handler)  # type: ignore[arg-type]

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
    # 微人大 OAuth 的回调在其平台登记为 .../api/legacy/users/auth/oauth/callback/ruc。
    # 上游 API 网关只剥掉 /api/ 前缀，后端因此收到 /legacy/users/auth/oauth/callback/ruc，
    # 与常规路由 /users/auth/oauth/callback/ruc 不匹配。复用同一处理函数在该 legacy
    # 前缀路径上再注册一次，让回调无需改网关或微人大白名单即可到达后端。
    app.add_api_route(
        "/legacy/users/auth/oauth/callback/{providerId}",
        users.handle_oauth_callback,
        methods=["GET"],
        include_in_schema=False,
    )
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
    app.include_router(agent_tools.router)
    app.include_router(threads.router)
    app.include_router(blocks.router)

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
    async def _check_security_config() -> None:
        if settings.environment not in ("development", "test"):
            if settings.jwt_secret == "dev-secret":
                raise RuntimeError("FATAL: JWT_SECRET must be changed from default in production")
            if not settings.realname_encryption_key:
                raise RuntimeError("FATAL: REALNAME_ENCRYPTION_KEY must be set in production")

    @app.on_event("startup")
    async def _start_notification_jobs() -> None:
        await finalizer.start()

    @app.on_event("startup")
    async def _setup_search_indices() -> None:
        from app.domain.search.meilisearch_service import setup_indices

        try:
            await setup_indices()
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
