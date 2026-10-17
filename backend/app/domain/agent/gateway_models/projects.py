"""项目页：每个项目的项目密钥、网关花费与平台算力余额，以及预算这道刹车的设置。

额度这一列取的是平台自己的算力账（`UsageService.project_credits_batch`），不是网关
密钥上的 `max_budget` —— 后者是刹车值，两者不等时页面上分开显示（`budget_derived_usd`
与 `budget_override_usd`），`set_budget` 改的是刹车那一个。
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.agent.gateway_admin import AdminKey, ModelUsage, UsageWindow
from app.domain.agent.gateway_models._shared import _DEFAULT_DAYS, _WRITE_ERRORS
from app.domain.agent.gateway_models_helpers import _usage_to_dict
from app.domain.project.services import ProjectService
from app.domain.usage.services import UsageService

if TYPE_CHECKING:
    from app.domain.agent import gateway_models as pkg


async def projects(service: pkg.GatewayModelsService, *, days: int) -> dict:
    start, end, _, _ = service._window(days)
    await service._gateway_up()
    keys = await service._keys_raw()
    usage = await service._usage_window(start, end)
    items = await service._project_items(keys, usage)
    return {
        "window": service._window_dict(days, start, end),
        "projects": items,
        "totals": {
            "projects": len(items),
            "with_key": sum(1 for i in items if i["has_key"]),
            # 「超预算」的判据是花掉的钱已经够到刹车值 —— 不是「快到了」。
            "over_budget": sum(
                1
                for i in items
                if i["max_budget_usd"] is not None
                and i["gateway_spend_usd"] >= i["max_budget_usd"]
            ),
            "unlimited": sum(1 for i in items if i["credits"]["unlimited"]),
        },
    }


async def _project_items(
    service: pkg.GatewayModelsService, keys: list[AdminKey], usage: UsageWindow
) -> list[dict]:
    by_alias = {k.alias: k for k in keys}
    by_user = {k.user_id: k for k in keys if k.user_id}
    projects, _total = await ProjectService(service._db).list_all()
    # 额度汇总一次批量取（teams 归属一条 JOIN、grants 一条 IN），不是逐项目
    # 各查一遍 —— 项目多的时候，2N+1 次串行往返和 3 次之间的差就是这段
    # 页面加载的肉眼差。口径与逐项目的 `project_credits` 逐字段相等
    # （`tests/unit/test_credits_batch.py` 钉着这件事）。
    summaries = await UsageService(service._db).project_credits_batch(projects)
    return [
        service._project_item(project, by_alias, by_user, usage, summaries[project.id])
        for project in projects
    ]


def _project_item(
    project, by_alias, by_user, usage: UsageWindow, summary: dict
) -> dict:
    key = by_alias.get(f"project-{project.id}") or by_user.get(f"project:{project.id}")
    # 刹车值的「应有」值：key 已花的加上还能花的额度折成美元；方案不限量时没有
    # 这个数，此时 key 上任何 max_budget 都是一次显式的覆盖。
    derived = summary["gateway_budget_usd"]
    max_budget = key.max_budget if key else None
    override = max_budget if max_budget is not None and max_budget != derived else None
    u: ModelUsage | None = usage.by_key.get(key.key_hash) if key else None
    return {
        "project_id": str(project.id),
        "name": project.name,
        "key_alias": key.alias if key else f"project-{project.id}",
        "has_key": key is not None,
        "gateway_spend_usd": key.spend if key else 0.0,
        "max_budget_usd": max_budget,
        "budget_derived_usd": derived,
        "budget_override_usd": override,
        "credits": {
            "total": summary["credits_total"],
            "used": summary["credits_used"],
            "remaining": summary["credits_remaining"],
            "unlimited": summary["unlimited"],
        },
        "usage": _usage_to_dict(u),
    }


async def set_budget(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    project_id: uuid.UUID,
    max_budget_usd: float | None,
) -> dict:
    before = await service._snapshot_key(project_id)
    try:
        item = await service._set_budget(project_id, max_budget_usd)
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action="project.budget",
            target=str(project_id),
            result="failed",
            detail=str(exc),
            before=before,
            after={"max_budget_usd": max_budget_usd},
        )
        raise
    await service._record(
        handle=handle,
        action="project.budget",
        target=str(project_id),
        result="ok",
        before=before,
        after={"max_budget_usd": max_budget_usd},
    )
    return item


async def _set_budget(
    service: pkg.GatewayModelsService,
    project_id: uuid.UUID,
    max_budget_usd: float | None,
) -> dict:
    project = await ProjectService(service._db).get(project_id)
    if project is None:
        raise NotFoundError(say("projectNotFoundById", project_id=project_id))
    await service._gateway_up()
    keys = await service._keys_raw()
    by_alias = {k.alias: k for k in keys}
    by_user = {k.user_id: k for k in keys if k.user_id}
    key = by_alias.get(f"project-{project.id}") or by_user.get(f"project:{project.id}")
    if key is None:
        raise BadRequestError(say("projectNoGatewayKey", name=project.name))
    await service._admin.set_key_budget(  # type: ignore[union-attr]
        key.key_hash, max_budget_usd
    )
    await service._after_write()
    start, end, _, _ = service._window(_DEFAULT_DAYS)
    usage = await service._usage_window(start, end)
    keys = await service._keys_raw()
    by_alias = {k.alias: k for k in keys}
    by_user = {k.user_id: k for k in keys if k.user_id}
    summary = await UsageService(service._db).project_credits(project.id)
    return service._project_item(project, by_alias, by_user, usage, summary)
