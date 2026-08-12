"""Aggregation routes — project overview (§7.2) and Space board (§7.3)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.core.db import get_db
from app.domain.dashboard.services import DashboardService
from app.domain.usage.repositories import ComputeGrantRepository, UsageRepository

router = APIRouter(prefix="/api", tags=["dashboard"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/projects/{project_id}/overview")
async def project_overview(
    project_id: uuid.UUID, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """事维度总览 — for a verified caller; 等你处理的事 shows THEIR items plus
    broadcasts, never other members' mailboxes (those ids/titles used to leak
    here unauthenticated)."""
    viewer = await resolver.resolve_recipient(
        requested=None, project_id=project_id, allow_anonymous=False
    )
    return ok(await DashboardService(db).project_overview(project_id, viewer=viewer))


@router.get("/spaces/{space_id}/dashboard")
async def space_dashboard(space_id: int, db: DbSession) -> dict:
    return ok(await DashboardService(db).space_board(space_id))


@router.get("/projects/{project_id}/members/{user_handle}/summary")
async def member_summary(
    project_id: uuid.UUID, user_handle: str, db: DbSession
) -> dict:
    """成员页 (spec §7.2): one member's topics + waiting items + role."""
    return ok(await DashboardService(db).member_summary(project_id, user_handle))


@router.get("/projects/{project_id}/usage")
async def project_usage(project_id: uuid.UUID, db: DbSession) -> dict:
    """资源用量 (spec §9.1): aggregated token/cost for the whole project."""
    return ok(await UsageRepository(db).for_project(project_id))


@router.get("/projects/{project_id}/credits")
async def project_credits(project_id: uuid.UUID, db: DbSession) -> dict:
    """算力额度 (spec §9.1): grant balance for the project. A project with no
    grants (no linked institutional task) is unlimited (spec §4 自治)."""
    summary = await ComputeGrantRepository(db).summary(project_id)
    return ok(
        {
            "unlimited": summary["unlimited"],
            "credits_total": summary["credits_total"],
            "credits_used": summary["credits_used"],
            "credits_remaining": summary["credits_remaining"],
            "grants": [
                {
                    "id": str(g.id),
                    "source_task_id": (
                        str(g.source_task_id) if g.source_task_id else None
                    ),
                    "credits_total": g.credits_total,
                    "credits_used": g.credits_used,
                    "created_at": g.created_at.isoformat(),
                }
                for g in summary["grants"]
            ],
        }
    )


@router.get("/projects/{project_id}/contributions")
async def contributions(project_id: uuid.UUID, db: DbSession) -> dict:
    """贡献统计 (spec §10.1): human vs AI + per author."""
    return ok(await DashboardService(db).contributions(project_id))


@router.get("/users/{handle}/profile")
async def user_profile(handle: str, db: DbSession) -> dict:
    """个人主页 (spec §7.2): cross-project profile / portfolio."""
    return ok(await DashboardService(db).user_profile(handle))
