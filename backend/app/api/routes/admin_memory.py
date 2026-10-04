"""记忆那两件后台活：旧表迁移（报告 → 复核 → 落笔）和「记忆正文到底被读过几次」。

两件事放在一个模块里，因为它们问的是同一张表上的两个问题，而且都是**给运营的人
看的**，不是给房间里的 agent 用的：迁移是一次性的（旧表搬进文件树，搬完就完），
读数是试点的（「一周里几乎没人读记忆正文」这件事要有个数，不能靠感觉）。`main.py`
的自动发现是「一个模块一个 router」，所以两件都不大、都要一个门的东西合成一个模块
——门是共用的 `admin_common.PlatformAdminDep`。

迁移那半边的规矩在 `domain/memory/migration.py`，这里只把它的四个动作露出来：

    POST /admin/memory/migration/projects/{project_id}/dry-run   读旧表、问模型、出报告
    GET  /admin/memory/migration/projects/{project_id}/plans     这个项目搬过几次
    GET  /admin/memory/migration/plans/{plan_id}                 一份报告（全文）
    POST /admin/memory/migration/plans/{plan_id}/approve          复核（只认复核人）
    POST /admin/memory/migration/plans/{plan_id}/apply            落笔

**dry-run 一次可能要几分钟**：它要读整个项目的旧记忆、问一遍模型。这是有意的——它
是运营动作，走脚本（`scripts/memory_migration.py`）比走 HTTP 更合适；接口留着是为了
复核与落笔能在一个页面上点，而不是为了让人拿它当高频调用。

读数那半边：`GET /admin/memory/reads`。见 `domain/memory/reads.py` 上面那一段。
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.api.routes.admin_common import PlatformAdminDep
from app.core.config import settings
from app.core.db import get_db
from app.domain.memory.migration_service import MemoryMigrationService
from app.domain.memory.models import MemoryMigrationPlan
from app.domain.memory.reads import body_reads

router = APIRouter(prefix="/admin/memory", tags=["admin"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _file_path(item: dict) -> str:
    """计划里那个文件的完整路径（`project/x.md` / `private/alice/y.md`）。"""
    owner = item["owner"]
    return f"{item['scope']}/{owner + '/' if owner else ''}{item['path']}"


def _plan_out(row: MemoryMigrationPlan, *, with_report: bool = False) -> dict:
    payload = {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "status": row.status,
        "created_by": row.created_by,
        "approved_by": row.approved_by,
        "sources": len(row.source_ids),
        "files": len(row.files),
        "suggestions": len(row.suggestions),
        "indexes": len(row.indexes),
        "summary": row.summary,
        "created_at": row.created_at.isoformat(),
        "approved_at": row.approved_at.isoformat() if row.approved_at else None,
        "applied_at": row.applied_at.isoformat() if row.applied_at else None,
    }
    if with_report:
        # 报告是这份东西的正身（人复核的就是它），另外两样是它的明细：改哪几个文件、
        # 只是建议的那些。分开给，是因为复核的人先读报告，再按需看明细。
        payload["report"] = row.report
        payload["files_preview"] = [
            {
                "path": _file_path(item),
                "is_new": item["is_new"],
                "sources": item["sources"],
                "content": item["content"],
            }
            for item in row.files
        ]
        payload["suggestions"] = row.suggestions
        payload["reviewer"] = settings.memory_migration_reviewer
    return payload


@router.post("/migration/projects/{project_id}/dry-run")
async def dry_run_migration(
    project_id: uuid.UUID,
    admin: PlatformAdminDep,
    db: DbSession,
):
    """读旧表、问模型、出一份报告。**这一步一个字都不写进新树。**"""
    service = MemoryMigrationService(db)
    row = await service.dry_run(project_id, by=admin)
    await db.commit()
    return ok(_plan_out(row, with_report=True), "报告好了，等复核")


@router.get("/migration/projects/{project_id}/plans")
async def list_migration_plans(
    project_id: uuid.UUID,
    admin: PlatformAdminDep,
    db: DbSession,
):
    plans = await MemoryMigrationService(db).plans_of(project_id)
    return ok([_plan_out(row) for row in plans])


@router.get("/migration/plans/{plan_id}")
async def get_migration_plan(
    plan_id: uuid.UUID,
    admin: PlatformAdminDep,
    db: DbSession,
):
    row = await MemoryMigrationService(db).get_or_404(plan_id)
    return ok(_plan_out(row, with_report=True))


@router.post("/migration/plans/{plan_id}/approve")
async def approve_migration(
    plan_id: uuid.UUID,
    admin: PlatformAdminDep,
    db: DbSession,
):
    """复核人点头。**别人点不动**（见 `settings.memory_migration_reviewer`）。"""
    row = await MemoryMigrationService(db).approve(plan_id, by=admin)
    await db.commit()
    return ok(_plan_out(row, with_report=True), "复核过了，可以落笔")


@router.post("/migration/plans/{plan_id}/apply")
async def apply_migration(
    plan_id: uuid.UUID,
    admin: PlatformAdminDep,
    db: DbSession,
):
    """按复核过的那份计划写进记忆树。一条冲突就整次不写。"""
    row = await MemoryMigrationService(db).apply(plan_id, by=admin)
    await db.commit()
    return ok(_plan_out(row), "搬完了")


@router.get("/reads")
async def memory_body_reads(
    admin: PlatformAdminDep,
    db: DbSession,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
    project_id: uuid.UUID | None = None,
):
    """每个项目每天有多少次**正文**读取（`Read` 打到记忆目录里的文件）。

    索引每轮注入，正文要自己去读——这个数就是「正文到底有没有人读」。试点要回答的
    是「一周下来是不是接近 0」，所以默认看七天，按天给。
    """
    until = datetime.now(UTC)
    since = until - timedelta(days=days)
    rows = await body_reads(db, since=since, until=until, project_id=project_id)
    return ok(
        {
            "days": days,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "projects": [row.as_dict() for row in rows],
        }
    )
