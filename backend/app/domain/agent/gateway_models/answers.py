"""网关侧的答案：可达性、模型表、密钥表、用量窗口，以及写完之后让它们跟上。

这些读法只有网关能答，`_gateway_up` 是它们共同的门；每一份都过 `cache._cached`，
所以页面一小时里问网关的次数与缓存 TTL 挂钩，而不是与请求数挂钩。写族那边只用
`_after_write`：先失效这份缓存，再让选择器那份目录重读网关。

`_snapshot_model` / `_snapshot_key` 是写前的对照快照，回答审计的 `before`；它们读的
也是这几份答案，读不到就给 None，不把写本身挡在门外。
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date
from typing import TYPE_CHECKING

from app.core.config import settings
from app.core.errors import NotFoundError
from app.core.sentences import say
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_admin import (
    AdminKey,
    AdminModel,
    GatewayAdminError,
    GatewayUnreachable,
    ModelUsage,
    UsageWindow,
)
from app.domain.agent.gateway_models.cache import _cached, reset_cache
from app.domain.agent.gateway_models_helpers import (
    LEDGER_START,
    _key_snapshot,
    _model_snapshot,
    _spend_cap,
)

if TYPE_CHECKING:
    from app.domain.agent import gateway_models as pkg


async def _gateway_up(service: pkg.GatewayModelsService) -> None:
    """网关管理 API 可达吗？不可达就直接抛 —— 下面每个读法都只有网关能答。"""
    if service._admin is None:
        raise GatewayUnreachable(say("gatewayAdminNotConfigured"))
    if await service._readiness() is None:
        raise GatewayUnreachable(say("gatewayUnreachable"))


async def _readiness(service: pkg.GatewayModelsService) -> str | None:
    assert service._admin is not None
    return await _cached("readiness", service._admin.readiness)


async def _models_raw(service: pkg.GatewayModelsService) -> list[AdminModel]:
    assert service._admin is not None
    return await _cached("models", service._admin.models)


async def _keys_raw(service: pkg.GatewayModelsService) -> list[AdminKey]:
    assert service._admin is not None
    return await _cached("keys", service._admin.keys)


async def _usage_window(
    service: pkg.GatewayModelsService,
    start: date,
    end: date,
    *,
    upstream: str | None = None,
) -> UsageWindow:
    admin = service._admin
    assert admin is not None
    key = f"usage:{start.isoformat()}:{end.isoformat()}:{upstream or ''}"
    return await _cached(
        key,
        lambda: admin.usage(start.isoformat(), end.isoformat(), upstream=upstream),
    )


async def _cap_spend(
    service: pkg.GatewayModelsService, models: list[AdminModel], end: date
) -> dict[str, ModelUsage]:
    """设了消费上限的每个上游模型的终身账：从 `LEDGER_START` 到 ``end``，与
    spend_cap.py 拦截时同一张表、同一种求和（按上游模型名、不分窗口）。

    每个上游只读它自己名下的行：整本账一次读回要从网关拉好几 MB、一秒半，而页面
    要的只是这几个上游各自的总数。没有线路设上限就一次都不读。"""
    upstreams = sorted(
        {s.upstream_model for m in models for s in m.sources if _spend_cap(s)}
    )
    windows = await asyncio.gather(
        *(service._usage_window(LEDGER_START, end, upstream=u) for u in upstreams)
    )
    return {
        u: w.by_upstream[u]
        for u, w in zip(upstreams, windows, strict=True)
        if u in w.by_upstream
    }


async def _require_models(service: pkg.GatewayModelsService) -> list[AdminModel]:
    await service._gateway_up()
    return await service._models_raw()


async def _current_model(service: pkg.GatewayModelsService, name: str) -> AdminModel:
    models = await service._require_models()
    found = next((m for m in models if m.name == name), None)
    if found is None:
        raise NotFoundError(say("gatewayModelNotFound", name=name))
    return found


async def _snapshot_model(service: pkg.GatewayModelsService, name: str) -> dict | None:
    """写前那条模型的当前状态，给审计的 `before` 回答「原来是什么」。

    读不到（网关不可达、模型不存在）就返回 None：`before` 缺一列不该把写操作本
    身挡在门外 —— 这次读失败随后会在 `_update` / `_set_blocked` 里再发生一次，
    照常落一条 failed，该记的原因不会因此丢掉。
    """
    try:
        return _model_snapshot(await service._current_model(name))
    except (GatewayAdminError, NotFoundError):
        return None


async def _snapshot_key(
    service: pkg.GatewayModelsService, project_id: uuid.UUID
) -> dict | None:
    """写前那把项目密钥的预算状态，给审计的 `before`。读不到就给 None。"""
    if service._admin is None:
        return None
    try:
        keys = await service._keys_raw()
    except GatewayAdminError:
        return None
    by_alias = {k.alias: k for k in keys}
    by_user = {k.user_id: k for k in keys if k.user_id}
    key = by_alias.get(f"project-{project_id}") or by_user.get(f"project:{project_id}")
    return None if key is None else _key_snapshot(key)


async def _after_write(service: pkg.GatewayModelsService) -> None:
    """写成功后让缓存与目录都跟上：先失效，再让选择器那份目录重读网关。"""
    reset_cache()
    await gateway_catalog.refresh(service._catalog_gateway())


def _catalog_gateway(service: pkg.GatewayModelsService) -> LlmGateway | None:
    if service._gateway is not None:
        return service._gateway
    if not (settings.llm_gateway_admin_base and settings.llm_gateway_admin_key):
        return None
    return LlmGateway(settings.llm_gateway_admin_base, settings.llm_gateway_admin_key)
