"""模型：列表页、详情页、模型本身的增删改停用，以及审计的读法。

读的那半边把网关的模型表与用量窗口拼成页面要的形状（`_item` 在 `_shared`）；写的那
半边两条不变式（config 来源拒写、上架必须有价）在 `_add` / `_update` / `_set_blocked`
里强制，每个公开写都先记账、成功后再失效缓存。

`detail` 顺带回答「这个模型在平台自己账上用了多少」—— 那只是带脚注的对照，见
`_platform_usage` 的说明；模型用量以网关账本为准。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.agent.gateway_admin import UsageWindow
from app.domain.agent.gateway_models import roles
from app.domain.agent.gateway_models._shared import _MID_SYSTEM, _WRITE_ERRORS
from app.domain.agent.gateway_models.guards import (
    _refuse_unlisting,
    _refuse_unserving,
)
from app.domain.agent.gateway_models_helpers import (
    _api_base_for,
    _daily_model_row,
    _offered,
    _prices_for_gateway,
    _refuse_cleared_prices,
    _refuse_config_write,
    _require_another_offered,
    _require_price_when_selectable,
    _require_prices,
    _source_prices,
    _translated_to_chat,
    _upstream_of,
    _usage_to_dict,
    _validate_api_base,
    _validate_name,
)
from app.domain.agent.models import GatewayAdminAudit
from app.domain.agent.schemas import ModelCreate, ModelUpdate
from app.domain.usage.services import UsageService

if TYPE_CHECKING:
    from app.domain.agent import gateway_models as pkg

# 查「这个模型在平台自己账上用了多少」时，`UsageService.by_model` 是一次
# top-N 聚合（模型是低基数维度）。给一个比任何真实部署的模型数都大的上界，就能把
# 目标那一行稳定地捞出来；真正的目的是**复用既有 SQL**，不在这里另写一份按模型的
# 聚合（契约 §6 明确不要拿 `resource_usage` 做模型用量，这里只作为带脚注的对照）。
_PLATFORM_MODEL_SCAN = 200

# 新模型第一条线路的优先级。没有 order 的线路只在所有有 order 的都冷却时才轮到，
# 所以第一条也给一个数，后加的线路才排得进它前后。
_FIRST_ORDER = 1


async def listing(service: pkg.GatewayModelsService, *, days: int) -> dict:
    start, end, _, _ = service._window(days)
    window = service._window_dict(days, start, end)
    fetched_at = service._now().isoformat()

    if service._admin is None:
        # 没配管理凭据是**受支持的部署**（见 `deps.get_llm_gateway`）：页面照开，
        # 只是模型表空着、并明说原因。把它当 503 会让这类部署整个后台上不去。
        return {
            "gateway": {
                "reachable": False,
                "readiness": None,
                "admin_configured": False,
                "detail": "未配置网关管理密钥（llm_gateway_admin_base / key）",
                "fetched_at": fetched_at,
            },
            "window": window,
            "totals": _usage_to_dict(None),
            "models": [],
        }

    # 配了凭据但不可达 → 交给路由转 503。列表页的头等事就是这个列表，答不出来
    # 就没有「部分正确」可给：一张空表会被读成「这里一个模型都没有」。
    models = await service._require_models()
    usage = await service._usage_window(start, end)
    ledger = await service._cap_spend(models, end)
    await roles.refresh(service._db)
    return {
        "gateway": {
            "reachable": True,
            "readiness": await service._readiness(),
            "admin_configured": True,
            "detail": None,
            "fetched_at": fetched_at,
        },
        "window": window,
        "totals": _usage_to_dict(usage.totals),
        "models": [service._item(m, usage, ledger) for m in models],
    }


async def detail(service: pkg.GatewayModelsService, *, name: str, days: int) -> dict:
    start, end, since, until = service._window(days)
    model = await service._current_model(name)
    usage = await service._usage_window(start, end)
    ledger = await service._cap_spend([model], end)
    await roles.refresh(service._db)
    return {
        "model": service._item(model, usage, ledger),
        "series": service._series(name, usage),
        "platform_usage": await service._platform_usage(name, since, until),
    }


def _series(name: str, usage: UsageWindow) -> list[dict]:
    """折线取的是**同一次** `_usage_window` 里那份逐日明细。

    从 `usage.daily` 里切，而不是另调一次网关按模型取：另调既绕过缓存、又多打一
    次网关，更坏的是让「模型总用量」（来自 `by_model` 的累加）与「折线之和」（来
    自另一条路）成了两本账，对不上时页面自己打自己。同一次读里切出来，两者同源。

    该模型某天没有用量就给 0，而不是断线 —— 折线不该在一个安静的日子上断开。
    """
    out: list[dict] = []
    for day in usage.daily:
        row = _daily_model_row(name, day)
        out.append(
            {
                "date": day.get("date", ""),
                "spend_usd": row.spend_usd if row is not None else 0.0,
                "requests": row.requests if row is not None else 0,
                "tokens": row.total_tokens if row is not None else 0,
            }
        )
    return out


async def _platform_usage(
    service: pkg.GatewayModelsService, name: str, since: datetime, until: datetime
) -> dict:
    """平台自己账上这个模型的用量 —— 只作对照，**不是**模型用的那个数。

    网关流量的模型归因在 `resource_usage` 里是坏的（写的是部署默认模型，契约
    §0），所以这个数常常是 0 而网关那边有真用量。页面上它带着 `note` 出现，读
    的人该看网关那一列 —— 这一句就是那个提醒，不是装饰。
    """
    rows = await UsageService(service._db).by_model(
        since=since, until=until, limit=_PLATFORM_MODEL_SCAN
    )
    row = next((r for r in rows if r["model"] == name), None)
    return {
        "calls": int(row["calls"]) if row else 0,
        "tokens": int(row["tokens"]) if row else 0,
        "cost_usd": float(row["cost_usd"]) if row else 0.0,
        "unpriced_tokens": int(row["unpriced_tokens"]) if row else 0,
        "note": "网关流量的按模型归因以网关账本为准",
    }


async def audit(service: pkg.GatewayModelsService, *, limit: int) -> dict:
    stmt = (
        select(GatewayAdminAudit)
        .order_by(GatewayAdminAudit.created_at.desc())
        .limit(limit)
    )
    rows = (await service._db.scalars(stmt)).all()
    return {
        "items": [
            {
                "created_at": row.created_at.isoformat(),
                "actor_handle": row.actor_handle,
                "action": row.action,
                "target": row.target,
                "result": row.result,
                "detail": row.detail,
                # 写入时已 `_redact`，读侧直接给 —— 它们回答的是「改了什么」，
                # 审计区从「谁动了」升级成「动了什么」就靠这两个字段。
                "before": row.before,
                "after": row.after,
            }
            for row in rows
        ]
    }


async def add(
    service: pkg.GatewayModelsService, *, handle: str, payload: ModelCreate
) -> dict:
    data = payload.model_dump()
    name = str(data.get("name") or "")
    try:
        item = await service._add(name, data)
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action="model.add",
            target=name,
            result="failed",
            detail=str(exc),
            after=data,
        )
        raise
    await service._record(
        handle=handle, action="model.add", target=name, result="ok", after=data
    )
    return {"model": item}


async def _add(service: pkg.GatewayModelsService, name: str, data: dict) -> dict:
    _validate_name(name)
    upstream_model = _upstream_of(data)
    api_base = _api_base_for(upstream_model, data.get("api_base"))
    _validate_api_base(api_base)
    capabilities = dict(data.get("capabilities") or {})
    if _translated_to_chat(upstream_model):
        capabilities[_MID_SYSTEM] = False
    selectable = bool(data.get("selectable"))
    free = bool(data.get("free"))
    prices = _source_prices(data.get("prices"), free)
    _require_prices(prices, free)

    models = await service._require_models()
    if any(m.name == name for m in models):
        # 同名再建一条，网关会把它当成这个模型的又一条线路，却带着另一份展示元数据
        # —— 目录读哪一行就显示哪一份。加线路有自己的入口，它会把元数据抄齐。
        raise BadRequestError(say("gatewayModelExists", name=name))
    model_id = await service._admin.add_model(  # type: ignore[union-attr]
        name=name,
        upstream_model=upstream_model,
        api_base=api_base,
        api_key=data.get("api_key"),
        prices=_prices_for_gateway(prices),
        label=data.get("label"),
        selectable=selectable,
        capabilities=capabilities,
        tier=data.get("tier"),
        overseas=data.get("overseas"),
        order=data.get("order") or _FIRST_ORDER,
        free=free or None,
        upstream_headers=data.get("upstream_headers") or None,
        session_header=data.get("session_header") or None,
    )
    await service._after_write()
    return await service._read_back(name, data, model_id)


async def update(
    service: pkg.GatewayModelsService, *, handle: str, name: str, payload: ModelUpdate
) -> dict:
    data = payload.model_dump(exclude_unset=True)
    before = await service._snapshot_model(name)
    try:
        item = await service._update(name, data)
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action="model.update",
            target=name,
            result="failed",
            detail=str(exc),
            before=before,
            after=data,
        )
        raise
    await service._record(
        handle=handle,
        action="model.update",
        target=name,
        result="ok",
        before=before,
        after=data,
    )
    return {"model": item}


async def _update(service: pkg.GatewayModelsService, name: str, data: dict) -> dict:
    current = await service._current_model(name)
    _refuse_config_write(current)
    api_base = data.get("api_base")
    if api_base is not None:
        _validate_api_base(api_base)
    api_key = data.get("api_key")
    touches_key = bool(api_key) and not data.get("api_key_unchanged")
    touches_source = touches_key or any(
        field in data for field in ("upstream_model", "api_base", "prices")
    )
    if touches_source and len(current.sources) > 1:
        raise BadRequestError(say("sourceModelHasSeveralSources", name=name))

    # 不变式永远看**合并之后**的样子：把已上架模型的单价删成一半，和一开始就
    # 没价，对刹车是同一种损坏。有几条线路就逐个看。
    selectable = bool(data.get("selectable", current.selectable))
    if current.selectable and not selectable:
        await _refuse_unlisting(service, current)
    if _offered(current) and not selectable:
        _require_another_offered(await service._require_models(), name)
    prices = data.get("prices")
    for source in current.sources:
        if prices is None:
            _require_price_when_selectable(selectable, source.prices, source.free)
            continue
        effective = _source_prices(prices, source.free)
        _refuse_cleared_prices(source.prices, effective)
        _require_prices(effective, source.free)

    wanted = (data.get("capabilities") or {}).get(_MID_SYSTEM)
    if wanted is not False and any(
        _translated_to_chat(s.upstream_model) for s in current.sources
    ):
        if wanted is True:
            raise BadRequestError(say("sourceChatFoldsSystem", name=name))
        if data.get("capabilities") is not None:
            # 没提这一项的保存不能把它冲掉：有 Chat 线路的组，它只能是关。
            data = {
                **data,
                "capabilities": {**data["capabilities"], _MID_SYSTEM: False},
            }

    model_kwargs: dict[str, Any] = {}
    for field in ("label", "selectable", "tier", "overseas"):
        if field in data:
            model_kwargs[field] = data[field]
    if data.get("capabilities") is not None:
        model_kwargs["capabilities"] = data["capabilities"]
    source_kwargs: dict[str, Any] = {}
    for field in ("upstream_model", "api_base"):
        if field in data:
            source_kwargs[field] = data[field]
    # 编辑界面不回显上游凭据，所以「没填 key」是常态、不能当成「清空 key」。
    # 只有显式给了一个非空 key、且没声明 unchanged，才真的动凭据。
    if touches_key:
        source_kwargs["api_key"] = api_key
    if prices is not None:
        # 走到这里时这个模型至多一条线路（上面拒了多条的）。
        free = any(source.free for source in current.sources)
        source_kwargs["prices"] = _prices_for_gateway(_source_prices(prices, free))

    # 模型层的元数据每一行都写，否则这一行与那一行说的不是同一个模型。
    for source in current.sources:
        kwargs = {**model_kwargs, **source_kwargs}
        if kwargs:
            await service._admin.update_model(  # type: ignore[union-attr]
                model_id=source.source_id, **kwargs
            )
    await service._after_write()
    return await service._read_back(name, data, current.model_id)


async def delete(service: pkg.GatewayModelsService, *, handle: str, name: str) -> dict:
    try:
        await service._delete(name)
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action="model.delete",
            target=name,
            result="failed",
            detail=str(exc),
        )
        raise
    await service._record(
        handle=handle, action="model.delete", target=name, result="ok"
    )
    return {"deleted": True}


async def _delete(service: pkg.GatewayModelsService, name: str) -> None:
    current = await service._current_model(name)
    _refuse_config_write(current)
    await _refuse_unserving(service, current)
    for source in current.sources:
        await service._admin.delete_model(  # type: ignore[union-attr]
            source.source_id
        )
    await service._after_write()


async def set_blocked(
    service: pkg.GatewayModelsService, *, handle: str, name: str, blocked: bool
) -> dict:
    before = await service._snapshot_model(name)
    try:
        await service._set_blocked(name, blocked)
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action="model.blocked",
            target=name,
            result="failed",
            detail=str(exc),
            before=before,
            after={"blocked": blocked},
        )
        raise
    await service._record(
        handle=handle,
        action="model.blocked",
        target=name,
        result="ok",
        before=before,
        after={"blocked": blocked},
    )
    return {"blocked": blocked}


async def _set_blocked(
    service: pkg.GatewayModelsService, name: str, blocked: bool
) -> None:
    current = await service._current_model(name)
    # 网关不支持停用 config 模型 —— 页面上它对停用是只读的，这里也如实拒绝，
    # 而不是做一个只改平台数据的假象。
    _refuse_config_write(current, toggle=True)
    if blocked:
        await _refuse_unserving(service, current)
    for source in current.sources:
        await service._admin.set_blocked(  # type: ignore[union-attr]
            source.source_id, blocked
        )
    await service._after_write()
