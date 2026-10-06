"""管理后台「运行记录」：报错和平台自己处理掉的事，按种类合并（app.domain.run_record.admin）。"""

from typing import Literal

from fastapi import APIRouter, Query

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.core.errors import NotFoundError
from app.domain.run_record import admin

router = APIRouter(prefix="/admin/run-records", tags=["admin"])

Window = Literal["24h", "7d", "30d"]


@router.get("")
async def run_record_overview(
    db: DbSession,
    handle: PlatformAdminDep,
    window: Window = "24h",
    group: Literal["all", "errors", "recovered"] = "all",
    q: str | None = Query(None, max_length=200),
) -> dict:
    del handle
    return ok(await admin.overview(db, window=window, group=group, query=q))


@router.get("/detail")
async def run_record_detail(
    db: DbSession,
    handle: PlatformAdminDep,
    key: str = Query(..., max_length=2000),
    kind: str = Query(..., max_length=48),
    window: Window = "24h",
) -> dict:
    del handle
    found = await admin.detail(db, key=key, kind=kind, window=window)
    if found is None:
        raise NotFoundError("No such run records in this window")
    return ok(found)
