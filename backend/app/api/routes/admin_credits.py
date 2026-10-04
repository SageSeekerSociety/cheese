"""额度后台：方案、团队挂哪个方案、给团队发额度（#2397）。

全部要平台管理员（`PlatformAdminDep`），每一次写都落 `credit_admin_audit`：谁、对
哪个方案或团队、改前改后。把团队挂到 Reserve、给机构发采购的额度都走这里，不手跑
SQL、不跑脚本。
"""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query
from pydantic import AwareDatetime, BaseModel, Field

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.domain.agent_instance.configuration import model_choices
from app.domain.project.services import ProjectService
from app.domain.task.services import TaskService
from app.domain.usage.plans import PlanService

router = APIRouter(prefix="/admin", tags=["admin"])


class PlanWindow(BaseModel):
    """按小时（从团队第一次调用起算）或按周、按月（日历重置），二者给一个。"""

    hours: float | None = Field(default=None, gt=0)
    calendar: Literal["week", "month"] | None = None
    credits: float = Field(gt=0)


Audience = Literal["personal", "team", "both"]
Tier = Literal["included", "premium", "frontier"]


class PlanCreate(BaseModel):
    """新方案。`key` 不给就生成一个；`model_tiers` 缺省只许 included 档。"""

    key: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9-]{1,31}$")
    name: str = Field(min_length=1, max_length=64)
    audience: Audience = "both"
    credits_per_period: float | None = Field(default=None, ge=0)
    windows: list[PlanWindow] = []
    model_tiers: list[Tier] | None = ["included"]
    rank: int = 0


class PlanUpdate(BaseModel):
    """只传要改的；缺的不动。改动从下一期起生效，本期已发的包不变。"""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    audience: Audience | None = None
    credits_per_period: float | None = Field(default=None, ge=0)
    windows: list[PlanWindow] | None = None
    model_tiers: list[Tier] | None = None
    rank: int | None = None


class TeamPlanUpdate(BaseModel):
    plan_key: str = Field(min_length=1, max_length=32)


class GrantCreate(BaseModel):
    credits: float = Field(gt=0)
    # 带时区；不带时区的时间说不清是哪一刻，直接 422。
    expires_at: AwareDatetime | None = None
    # 为什么发（合同号、补偿）；只有管理员看得到。
    reason: str | None = Field(default=None, max_length=500)


@router.get("/plans")
async def list_plans(db: DbSession, handle: PlatformAdminDep) -> dict:
    return ok({"plans": await PlanService(db).plans()})


@router.get("/plans/models")
async def plan_models(handle: PlatformAdminDep) -> dict:
    """The plan editor uses the same catalogue as the deployment's model picker,
    including subscription models, not the gateway's management inventory."""
    return ok(
        {
            "models": [
                {key: choice[key] for key in ("id", "label", "tier")}
                for choice in model_choices(None)
            ]
        }
    )


@router.post("/plans", status_code=201)
async def create_plan(
    body: PlanCreate, db: DbSession, handle: PlatformAdminDep
) -> dict:
    plan = await PlanService(db).create_plan(handle=handle, data=body.model_dump())
    await db.commit()
    return ok(plan)


@router.put("/plans/{key}")
async def update_plan(
    key: Annotated[str, Path(max_length=32)],
    body: PlanUpdate,
    db: DbSession,
    handle: PlatformAdminDep,
) -> dict:
    data = body.model_dump(exclude_unset=True)
    # `model_tiers: null` 是「不限档」，和没传是两件事，所以按 exclude_unset 收。
    plan = await PlanService(db).update_plan(handle=handle, key=key, data=data)
    await db.commit()
    return ok(plan)


@router.delete("/plans/{key}")
async def delete_plan(
    key: Annotated[str, Path(max_length=32)],
    db: DbSession,
    handle: PlatformAdminDep,
) -> dict:
    await PlanService(db).delete_plan(handle=handle, key=key)
    await db.commit()
    return ok({"key": key})


@router.get("/teams")
async def list_teams(
    db: DbSession,
    handle: PlatformAdminDep,
    q: str | None = Query(default=None, max_length=100),
    plan: str | None = Query(default=None, max_length=32),
    kind: Literal["personal", "team"] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """``plan`` keeps the teams on that plan; ``kind`` keeps personal or shared
    teams. Both narrow the list before it is paged, so ``total`` counts them."""
    return ok(
        await PlanService(db).teams(
            query=q,
            page=page,
            page_size=page_size,
            plan_key=plan,
            personal=None if kind is None else kind == "personal",
        )
    )


@router.get("/teams/{teamId}")
async def team_credits(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    db: DbSession,
    handle: PlatformAdminDep,
) -> dict:
    team = await PlanService(db).team(team_id)
    # 定向额度用题目和项目的名字说清是哪一笔。
    projects, tasks = ProjectService(db), TaskService.of(db)
    for pack in team["packs"]:
        if pack["project_id"]:
            project = await projects.get(uuid.UUID(pack["project_id"]))
            pack["project_name"] = project.name if project else None
        if pack["task_id"] is not None:
            task = await tasks.get_task(pack["task_id"])
            pack["task_name"] = task.name if task else None
    return ok(team)


@router.get("/teams/{teamId}/history")
async def team_history(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    db: DbSession,
    handle: PlatformAdminDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return ok({"items": await PlanService(db).team_history(team_id, limit=limit)})


@router.put("/teams/{teamId}/plan")
async def set_team_plan(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    body: TeamPlanUpdate,
    db: DbSession,
    handle: PlatformAdminDep,
) -> dict:
    out = await PlanService(db).set_team_plan(
        handle=handle, team_id=team_id, key=body.plan_key
    )
    await db.commit()
    return ok(out)


@router.post("/teams/{teamId}/grants", status_code=201)
async def grant_team_credits(
    team_id: Annotated[int, Path(ge=1, alias="teamId")],
    body: GrantCreate,
    db: DbSession,
    handle: PlatformAdminDep,
) -> dict:
    pack = await PlanService(db).grant(
        handle=handle,
        team_id=team_id,
        credits=body.credits,
        expires_at=body.expires_at,
        reason=body.reason,
    )
    await db.commit()
    return ok(pack)


@router.get("/credits/audit")
async def credit_audit(
    db: DbSession,
    handle: PlatformAdminDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    return ok({"items": await PlanService(db).audit(limit=limit)})
