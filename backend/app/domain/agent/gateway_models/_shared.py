"""服务的公共零件：时间窗口、审计落行、写完读回、页面项拼装。

这几样被两处以上用，所以不属于任何一个读族或写族：`_window` / `_window_dict` 每个
读法都要一份，`_item` 既拼列表里的那一项、也拼写完读回的那一项，`_record` 是模型
写、线路写、项目预算三族共用的审计出口，`_read_back` 被模型写和线路写共用。

带 `service` 的这几个原来是 `GatewayModelsService` 的方法，用的是实例上的 `_db` /
`_now`；门面上同名方法只做一行转发。`_window_dict` 和 `_item` 只读传进来的日期与那
份网关答案，与实例状态无关，所以不收 `service` —— `_item` 仍然是门面上的方法，写回
那一步经对象调它。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import TYPE_CHECKING

from app.core import model_roles
from app.core.errors import BadRequestError, NotFoundError
from app.domain.agent.gateway_admin import GatewayAdminError
from app.domain.agent.gateway_models_helpers import (
    _config_yaml,
    _daily_tokens,
    _host_of,
    _item_from_payload,
    _offered_reasons,
    _redact,
    _scrub,
    _secrets_in,
    _source_item,
    _usage_to_dict,
)
from app.domain.agent.models import GatewayAdminAudit
from app.domain.day_windows import utc_now

if TYPE_CHECKING:
    from collections.abc import Mapping

    from app.domain.agent import gateway_models as pkg
    from app.domain.agent.gateway_admin import AdminModel, ModelUsage, UsageWindow

# 写操作的返回体要带一份 3.1 形状的模型项，而写接口的请求里没有 `days`。取列表页
# 的默认窗口，好让「刚写完看到的这一项」和「列表里那一项」是同一个口径。
_DEFAULT_DAYS = 7

# 页面上的能力键：能不能收对话中间的 system 消息（网关的
# `supports_mid_conversation_system`）。
_MID_SYSTEM = "mid_conversation_system"
_MID_SYSTEM_FIELD = "supports_mid_conversation_system"

# 写操作里「服务自己判死」或「网关判死」的几种错。都落审计，然后原样抛给路由。
_WRITE_ERRORS = (GatewayAdminError, BadRequestError, NotFoundError)


def _utcnow() -> datetime:
    return utc_now()


def _today(service: pkg.GatewayModelsService) -> date:
    now = service._now()
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).date()


def _window(
    service: pkg.GatewayModelsService, days: int
) -> tuple[date, date, datetime, datetime]:
    """``(start, end, since, until)``。

    `start..end`（**含**当天）是传给网关的闭区间日期 —— 网关的 `end_date` 含当天。
    `days` 读作「回看几天」，再连上今天：`days=7` 就是 `today-7 .. today`，与契约
    §3.1 冻结的示例（`2026-09-16 .. 2026-09-23`、当天 `2026-09-23`）逐字一致。
    `since..until`（半开）给平台自己的仓储用，覆盖同样的这些天。
    """
    today = service._today()
    start = today - timedelta(days=days)
    since = datetime.combine(start, dtime.min, tzinfo=UTC)
    until = datetime.combine(today, dtime.min, tzinfo=UTC) + timedelta(days=1)
    return start, today, since, until


def _window_dict(days: int, start: date, end: date) -> dict:
    return {
        "days": days,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


async def _read_back(
    service: pkg.GatewayModelsService, name: str, data: dict, model_id: str
) -> dict:
    """写完后按列表页同一口径读回这一项。

    读回失败、或网关刚写完还没把这一条反映到 `/model/info` 时，**不把已经成功
    的写报成失败**：退回用提交的内容拼一个最小项，页面下一拍刷新会拿到真值。
    """
    start, end, _, _ = service._window(_DEFAULT_DAYS)
    try:
        models = await service._models_raw()
        usage = await service._usage_window(start, end)
        found = next((m for m in models if m.name == name), None)
        ledger = await service._cap_spend([found], end) if found is not None else None
    except GatewayAdminError:
        return _item_from_payload(name, data, model_id)
    if found is not None:
        return service._item(found, usage, ledger)
    return _item_from_payload(name, data, model_id, usage.by_model.get(name))


async def _record(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    action: str,
    target: str,
    result: str,
    detail: str | None = None,
    before: dict | None = None,
    after: dict | None = None,
) -> None:
    secrets = _secrets_in(after) | _secrets_in(before)
    service._db.add(
        GatewayAdminAudit(
            actor_handle=(handle or "")[:64],
            target=(target or "")[:128],
            action=action[:32],
            result=result[:16],
            detail=_scrub(detail, secrets),
            before=_redact(before),
            after=_redact(after),
        )
    )
    await service._db.flush()
    # 立刻提交，独立于这次请求的成败：路由的 `get_db` 在异常时会回滚，而
    # 「失败也落一行」正是审计存在的理由 —— 让失败记录跟着回滚就等于没记。
    await service._db.commit()


def _item(
    model: AdminModel,
    usage: UsageWindow,
    ledger: Mapping[str, ModelUsage] | None = None,
) -> dict:
    offered = model.selectable and model.priced and not model.blocked
    item = {
        "name": model.name,
        "model_id": model.model_id,
        "label": model.label,
        "origin": model.origin,
        "blocked": model.blocked,
        "selectable": model.selectable,
        "priced": model.priced,
        "tier": model.tier,
        "overseas": model.overseas,
        "offered": offered,
        "roles": model_roles.pointing_at(model.name),
        "blocked_reasons": [] if offered else _offered_reasons(model),
        "unpriced_reason": None if model.priced else model.supports_notes,
        "upstream": {
            "model": model.upstream_model,
            "host": _host_of(model.api_base),
            "provider": model.provider,
        },
        "prices": dict(model.prices),
        "capabilities": dict(model.capabilities),
        "usage": _usage_to_dict(usage.by_model.get(model.name)),
        # 行内 sparkline 的逐日 token：从**同一次** `_usage_window` 里切
        # （与详情折线同源同账，零额外网关调用 —— 另调一次就多打一枪，
        # 还会让两处的数对不上）。
        "series": _daily_tokens(model.name, usage),
        # 这个模型的全部线路，按网关试它们的先后排好；config 的也在，只是只读。
        # ``ledger`` 是终身账（`answers._cap_spend`），给每条线路「上限已用多少」。
        "sources": [_source_item(source, ledger) for source in model.sources],
    }
    if model.origin == "config":
        # 只对 config 模型给这段可复制文本：它唯一的改法是编辑 config.yaml，
        # 页面上直接给出该粘贴的那几行，省得人照着重打一遍再打错。
        item["config_yaml"] = _config_yaml(model)
    return item
