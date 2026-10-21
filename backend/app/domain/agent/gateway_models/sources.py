"""线路：一个模型名下的一条部署（网关里的一个部署），以及围绕它的四个写。

加、改、删、停用、重排，都是「先记账、再落改动」同一个形状，所以共用 `_audited`
把成败那一行审计包起来；每个写把自己的动作写成一段 `work()` 闭包，`_audited` 只负责
跑它、记它、把结果原样交出。

线路不是模型：模型那一层的元数据（上架、档位、能力）同组每一行必须一致，所以这里
好几个地方要先从已有那一行抄，或整组一起改 —— `_fold_system_messages` 就是后者。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.core.errors import BadRequestError, NotFoundError
from app.core.sentences import say
from app.domain.agent.gateway_admin import (
    AdminModel,
    AdminSource,
    GatewayAdminError,
)
from app.domain.agent.gateway_models._shared import (
    _MID_SYSTEM,
    _MID_SYSTEM_FIELD,
    _WRITE_ERRORS,
)
from app.domain.agent.gateway_models.guards import _refuse_unserving_source
from app.domain.agent.gateway_models_helpers import (
    _api_base_for,
    _bare_upstream,
    _needs_price,
    _prices_for_gateway,
    _refuse_config_source,
    _require_price_when_selectable,
    _source_prices,
    _source_snapshot,
    _source_target,
    _spend_cap,
    _translated_to_chat,
    _upstream_of,
    _validate_api_base,
    _would_lead,
)
from app.domain.agent.schemas import SourceCreate, SourceUpdate, upstream_string

if TYPE_CHECKING:
    from app.domain.agent import gateway_models as pkg


async def _audited(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    action: str,
    target: str,
    work: Callable[[], Awaitable[dict]],
    before: dict | None = None,
    after: dict | None = None,
) -> dict:
    """跑一次线路写，成败都落一行审计（同模型那几个写，只是不再逐个抄一遍）。"""
    try:
        out = await work()
    except _WRITE_ERRORS as exc:
        await service._record(
            handle=handle,
            action=action,
            target=target,
            result="failed",
            detail=str(exc),
            before=before,
            after=after,
        )
        raise
    await service._record(
        handle=handle,
        action=action,
        target=target,
        result="ok",
        before=before,
        after=after,
    )
    return out


async def _current_source(
    service: pkg.GatewayModelsService, name: str, source_id: str
) -> tuple[AdminModel, AdminSource]:
    model = await service._current_model(name)
    source = next((s for s in model.sources if s.source_id == source_id), None)
    if source is None:
        raise NotFoundError(say("sourceNotFound", name=name, source=source_id))
    return model, source


async def _snapshot_source(
    service: pkg.GatewayModelsService, name: str, source_id: str
) -> dict | None:
    try:
        _, source = await service._current_source(name, source_id)
    except (GatewayAdminError, NotFoundError):
        return None
    return _source_snapshot(source)


async def _fold_system_messages(
    service: pkg.GatewayModelsService, model: AdminModel
) -> None:
    """这个模型要有一条走 OpenAI 兼容 Chat 的线路了：整组标
    `supports_mid_conversation_system: false`。

    Messages 翻成 chat/completions 时，对话中间的 system 消息会丢（实测 glm-5.2、
    MiMo）；标了这一项，Claude Code 把那段内容并进第一条用户消息。目录只读第一行，
    而请求可能落到组里任何一行，所以要么整组都标，要么都不标 —— config.yaml 的行
    改不了，没标就拒，让人先去改 config。
    """
    for source in model.sources:
        if source.catalogue.get(_MID_SYSTEM_FIELD) is False:
            continue
        if source.origin == "config":
            raise BadRequestError(say("sourceChatNeedsConfigFlag", name=model.name))
    for source in model.sources:
        if source.catalogue.get(_MID_SYSTEM_FIELD) is not False:
            await service._admin.update_model(  # type: ignore[union-attr]
                model_id=source.source_id, capabilities={_MID_SYSTEM: False}
            )


async def _same_upstream_caps(
    service: pkg.GatewayModelsService,
    *,
    upstream_model: str,
    cap: float | None,
    edited: str,
) -> list[str]:
    """The other runtime rows on ``upstream_model`` whose cap is not ``cap``.

    spend_cap.py counts a row's cap against everything its upstream model has
    been charged, whichever row or model name carried the calls, so one cap per
    upstream is the only setting that means anything: two rows with different
    caps would refuse at different totals of the same bill. A config.yaml row
    cannot be rewritten from here, so one that disagrees refuses the edit.
    """
    stale: list[str] = []
    for model in await service._require_models():
        for source in model.sources:
            if source.upstream_model != upstream_model or source.source_id == edited:
                continue
            if _spend_cap(source) == cap:
                continue
            if source.origin == "config":
                raise BadRequestError(
                    say(
                        "sourceSpendCapInConfig",
                        name=model.name,
                        upstream=upstream_model,
                    )
                )
            stale.append(source.source_id)
    return stale


async def add_source(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    name: str,
    payload: SourceCreate,
) -> dict:
    data = payload.model_dump()

    async def work() -> dict:
        model = await service._current_model(name)
        upstream_model = _upstream_of(data)
        api_base = _api_base_for(upstream_model, data.get("api_base"))
        _validate_api_base(api_base)
        free = bool(data.get("free"))
        prices = _source_prices(data.get("prices"), free)
        _require_price_when_selectable(_needs_price(model), prices, free)
        # 模型那一层的元数据从已有那一行抄：同组每一行必须一字不差。
        catalogue = dict(model.sources[0].catalogue if model.sources else {})
        # A cap and a cache rule belong to the upstream model: another row
        # on it (another key) keeps them, so it neither 400s nor escapes
        # the cap.
        catalogue.update(
            next(
                (
                    s.upstream_info
                    for s in model.sources
                    if s.upstream_model == upstream_model and s.upstream_info
                ),
                {},
            )
        )
        if _would_lead(model, data["order"]) and not data.get("make_primary"):
            raise BadRequestError(
                say("sourceWouldBePrimary", name=name, order=data["order"])
            )
        if _translated_to_chat(upstream_model):
            await service._fold_system_messages(model)
            catalogue[_MID_SYSTEM_FIELD] = False
        source_id = await service._admin.add_source(  # type: ignore[union-attr]
            name=name,
            catalogue=catalogue,
            upstream_model=upstream_model,
            order=data["order"],
            api_base=api_base,
            api_key=data.get("api_key"),
            prices=_prices_for_gateway(prices),
            free=free or None,
            upstream_headers=data.get("upstream_headers") or None,
            session_header=data.get("session_header") or None,
        )
        data["source_id"] = source_id
        if model.blocked:
            # 停用的模型加一条线路，不能顺手把模型启用了：新线路跟着停着，
            # 要用的时候整组一起启用。
            await service._admin.set_blocked(  # type: ignore[union-attr]
                source_id, True
            )
        await service._after_write()
        return {"model": await service._read_back(name, {}, model.model_id)}

    return await service._audited(
        handle=handle, action="source.add", target=name, work=work, after=data
    )


async def update_source(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    name: str,
    source_id: str,
    payload: SourceUpdate,
) -> dict:
    data = payload.model_dump(exclude_unset=True)
    before = await service._snapshot_source(name, source_id)

    async def work() -> dict:
        model, source = await service._current_source(name, source_id)
        _refuse_config_source(model, source)
        kwargs: dict[str, Any] = {"model_id": source.source_id}
        if "upstream_model" in data:
            kwargs["upstream_model"] = _upstream_of(data)
        elif data.get("protocol") is not None:
            # 只换协议、不换模型名：把现有的上游名套上新协议的前缀。
            bare = _bare_upstream(source)
            kwargs["upstream_model"] = upstream_string(data["protocol"], bare)
        upstream_model = kwargs.get("upstream_model", source.upstream_model)
        others: list[str] = []
        if "spend_cap_usd" in data:
            # Checked before anything is written: a refusal leaves every row as it was.
            cap = data["spend_cap_usd"]
            others = await _same_upstream_caps(
                service, upstream_model=upstream_model, cap=cap, edited=source_id
            )
            kwargs["spend_cap_usd"] = cap or 0.0
            # The audit names every row the edit wrote, not only the one asked for.
            data["spend_cap_also_on"] = others
        api_base = data.get("api_base")
        if api_base is None and "upstream_model" in kwargs:
            # 换了协议：已存的地址也要按新协议的写法收一遍。
            api_base = source.api_base
        if api_base is not None:
            api_base = _api_base_for(upstream_model, api_base)
            _validate_api_base(api_base)
            if api_base != source.api_base:
                kwargs["api_base"] = api_base
        if _translated_to_chat(upstream_model):
            await service._fold_system_messages(model)
            kwargs["capabilities"] = {_MID_SYSTEM: False}
        if data.get("api_key") and not data.get("api_key_unchanged"):
            kwargs["api_key"] = data["api_key"]
        free = source.free if data.get("free") is None else bool(data["free"])
        prices = data.get("prices")
        if prices is not None or data.get("free") is not None:
            effective = _source_prices(
                source.prices if prices is None else prices, free
            )
            _require_price_when_selectable(_needs_price(model), effective, free)
            kwargs["prices"] = _prices_for_gateway(effective)
            kwargs["free"] = free
        for field in ("order", "upstream_headers", "session_header"):
            if data.get(field) is not None:
                kwargs[field] = data[field]
        await service._admin.update_model(**kwargs)  # type: ignore[union-attr]
        for other in others:
            await service._admin.update_model(  # type: ignore[union-attr]
                model_id=other, spend_cap_usd=kwargs["spend_cap_usd"]
            )
        await service._after_write()
        return {"model": await service._read_back(name, {}, model.model_id)}

    return await service._audited(
        handle=handle,
        action="source.update",
        target=_source_target(name, source_id),
        work=work,
        before=before,
        after=data,
    )


async def delete_source(
    service: pkg.GatewayModelsService, *, handle: str, name: str, source_id: str
) -> dict:
    before = await service._snapshot_source(name, source_id)

    async def work() -> dict:
        model, source = await service._current_source(name, source_id)
        _refuse_config_source(model, source)
        await _refuse_unserving_source(service, model, source.source_id)
        await service._admin.delete_model(  # type: ignore[union-attr]
            source.source_id
        )
        await service._after_write()
        return {"deleted": True}

    return await service._audited(
        handle=handle,
        action="source.delete",
        target=_source_target(name, source_id),
        work=work,
        before=before,
    )


async def set_source_blocked(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    name: str,
    source_id: str,
    blocked: bool,
) -> dict:
    before = await service._snapshot_source(name, source_id)

    async def work() -> dict:
        model, source = await service._current_source(name, source_id)
        _refuse_config_source(model, source)
        if blocked:
            await _refuse_unserving_source(service, model, source.source_id)
        await service._admin.set_blocked(  # type: ignore[union-attr]
            source.source_id, blocked
        )
        await service._after_write()
        return {"blocked": blocked}

    return await service._audited(
        handle=handle,
        action="source.blocked",
        target=_source_target(name, source_id),
        work=work,
        before=before,
        after={"blocked": blocked},
    )


async def reorder_sources(
    service: pkg.GatewayModelsService,
    *,
    handle: str,
    name: str,
    orders: dict[str, int],
) -> dict:
    before = await service._snapshot_model(name)

    async def work() -> dict:
        model = await service._current_model(name)
        by_id = {s.source_id: s for s in model.sources}
        # 先全部判完再写：写到一半才发现某一条是 config 的，前面几条已经改了。
        for source_id in orders:
            source = by_id.get(source_id)
            if source is None:
                raise NotFoundError(say("sourceNotFound", name=name, source=source_id))
            _refuse_config_source(model, source)
        for source_id, order in orders.items():
            if by_id[source_id].order != order:
                await service._admin.update_model(  # type: ignore[union-attr]
                    model_id=source_id, order=order
                )
        await service._after_write()
        return {"model": await service._read_back(name, {}, model.model_id)}

    return await service._audited(
        handle=handle,
        action="source.order",
        target=name,
        work=work,
        before=before,
        after={"orders": dict(orders)},
    )
