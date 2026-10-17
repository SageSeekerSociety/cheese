"""`gateway_admin` 的解析层：网关给的形状 -> 页面要的形状。

管理客户端（``gateway_admin.GatewayAdmin``）只管把管理 API 上的东西原样取回来；把这些
JSON 折成页面契约的活全在这里——纯函数，不进网络、不留状态。

要造的形状与要读的线路键都从 ``gateway_admin_types`` 取；本模块不认识客户端，也不认识
上面那两层。名字按 ``app.domain.agent.gateway_admin._xxx`` 取照旧有效：那边把这些函数
导入进来用，也按原路径重导出。
"""

from collections.abc import Mapping
from urllib.parse import quote

import httpx

from app.core.sentences import say
from app.domain.agent.gateway import price_is_set
from app.domain.agent.gateway_admin_types import (
    _CAPABILITY_FIELDS,
    _PRICE_FIELDS,
    _UPSTREAM_INFO_KEYS,
    EXTRA_HEADERS,
    FREE,
    SESSION_HEADER,
    UPSTREAM_HEADERS,
    AdminKey,
    AdminModel,
    AdminSource,
    ModelUsage,
    catalogue_info,
)
from app.domain.agent.schemas import split_upstream
from app.domain.usage.pricing import declared_free

# ---------------------------------------------------------------------------
# 解析:网关给的形状 -> 页面要的形状。都写成小函数,免得一个方法里混着「怎么读」和
# 「怎么用」。字段缺失一律当「没有」,不猜、不补默认值——猜出来的价格比没有价格更坏。
# ---------------------------------------------------------------------------


def _short(exc: Exception) -> str:
    return " ".join(str(exc).split())[:200] or type(exc).__name__


def _quote(model_id: str) -> str:
    """模型 id 进 URL 路径前先转义。

    运行时模型的 id 是人给的字符串(契约 §0),可能带斜杠或空格,原样拼进路径会把
    路径切错、打到另一条路由上。``safe=""`` 连斜杠也一并编码。
    """
    return quote(model_id, safe="")


def _error_message(response: httpx.Response) -> str:
    """网关错误体里给人看的那句话。

    LiteLLM 有两种失败体,都要认:

    * ``{"detail": {"error": "..."}}``——老式 FastAPI 拒绝的形状(config 模型那条
      就是这个形状);普通错误则是 ``{"detail": "..."}``。
    * ``{"error": {"message": "...", "type": "auth_error", ...}}``——真正的
      ``ProxyException`` 走这条路(见 ``proxy_server.py`` 的 handler,它把
      ``exc.to_dict()`` 塞进顶层的 ``error`` 键),**没有 ``detail`` 键**。少认这一支,
      页面就会看到整坨 dict 的 repr,而不是那句人话。

    优先级:``error.message`` > ``detail.error`` > ``detail`` > ``error`` 的字符串化 >
    状态码。页面要原样显示原因,不能只报一个数字。
    """
    try:
        body = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return (
            text[:300]
            if text
            else say("gatewayAdminHttpStatus", status=response.status_code)
        )
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        detail = body.get("detail")
        if isinstance(detail, dict) and isinstance(detail.get("error"), str):
            return detail["error"]
        if isinstance(detail, str):
            return detail
        if detail is not None:
            return str(detail)
        if error is not None:
            return str(error)
    return (
        str(body)[:300]
        if body
        else say("gatewayAdminHttpStatus", status=response.status_code)
    )


def _num(value: object) -> float:
    return (
        float(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else 0.0
    )


def _int(value: object) -> int:
    return (
        int(value)
        if isinstance(value, int | float) and not isinstance(value, bool)
        else 0
    )


def _zero_usage() -> ModelUsage:
    return ModelUsage(0.0, 0, 0, 0, 0, 0, 0)


def _combine(a: ModelUsage, b: ModelUsage) -> ModelUsage:
    return ModelUsage(
        spend_usd=a.spend_usd + b.spend_usd,
        requests=a.requests + b.requests,
        failed_requests=a.failed_requests + b.failed_requests,
        prompt_tokens=a.prompt_tokens + b.prompt_tokens,
        completion_tokens=a.completion_tokens + b.completion_tokens,
        cache_read_tokens=a.cache_read_tokens + b.cache_read_tokens,
        total_tokens=a.total_tokens + b.total_tokens,
    )


def _accumulate(acc: dict[str, ModelUsage], key: str, usage: ModelUsage) -> None:
    current = acc.get(key)
    acc[key] = usage if current is None else _combine(current, usage)


def _usage(metrics: object) -> ModelUsage:
    """网关的 ``metrics`` 一段 -> 一条 ModelUsage。缺的字段当 0。"""
    m = metrics if isinstance(metrics, dict) else {}
    return ModelUsage(
        spend_usd=_num(m.get("spend")),
        requests=_int(m.get("api_requests")),
        failed_requests=_int(m.get("failed_requests")),
        prompt_tokens=_int(m.get("prompt_tokens")),
        completion_tokens=_int(m.get("completion_tokens")),
        cache_read_tokens=_int(m.get("cache_read_input_tokens")),
        total_tokens=_int(m.get("total_tokens")),
    )


def _entries(breakdown: object, key: str) -> dict:
    if not isinstance(breakdown, dict):
        return {}
    value = breakdown.get(key)
    return value if isinstance(value, dict) else {}


def _metrics_of(entry: object) -> object:
    return entry.get("metrics") if isinstance(entry, dict) else None


def _window_totals(payload: object, daily: list[dict]) -> ModelUsage:
    """平台级总量优先信网关自己的 ``metadata``;它缺了再把逐日加起来兜底。"""
    meta = payload.get("metadata") if isinstance(payload, dict) else None
    if isinstance(meta, dict):
        return ModelUsage(
            spend_usd=_num(meta.get("total_spend")),
            requests=_int(meta.get("total_api_requests")),
            failed_requests=_int(meta.get("total_failed_requests")),
            prompt_tokens=_int(meta.get("total_prompt_tokens")),
            completion_tokens=_int(meta.get("total_completion_tokens")),
            cache_read_tokens=_int(meta.get("total_cache_read_input_tokens")),
            total_tokens=_int(meta.get("total_tokens")),
        )
    total = _zero_usage()
    for day in daily:
        usage = day.get("totals")
        if isinstance(usage, ModelUsage):
            total = _combine(total, usage)
    return total


def _price_value(sources: tuple[object, ...], field: str) -> float | None:
    """两处单价里的第一个正数;全非正则取第一个数(含 0)。

    两处是 ``litellm_params`` 与 ``model_info``。与 ``price_is_set`` 同序:
    一个部署可能把费率写在任一处,只读一处会把另一处的模型误报成无价。
    """
    zero: float | None = None
    for src in sources:
        if not isinstance(src, dict):
            continue
        value = src.get(field)
        if isinstance(value, int | float) and not isinstance(value, bool):
            if value > 0:
                return float(value)
            if zero is None:
                zero = float(value)
    return zero


def _read_prices(params: object, info: object) -> dict[str, float]:
    out: dict[str, float] = {}
    for short, field in _PRICE_FIELDS.items():
        value = _price_value((params, info), field)
        if value is not None:
            out[short] = value
    return out


def _read_capabilities(info: dict) -> dict[str, bool]:
    """只带显式布尔位;网关给 None 是「不知道」,不能当成 False 报出去。"""
    out: dict[str, bool] = {}
    for short, field in _CAPABILITY_FIELDS.items():
        value = info.get(field)
        if isinstance(value, bool):
            out[short] = value
    return out


def _price_params(prices: Mapping[str, float | None] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    if not prices:
        return out
    for short, field in _PRICE_FIELDS.items():
        value = prices.get(short)
        if isinstance(value, int | float) and not isinstance(value, bool):
            out[field] = float(value)
    return out


def _capability_params(
    capabilities: Mapping[str, bool] | None,
) -> dict[str, bool]:
    out: dict[str, bool] = {}
    if not capabilities:
        return out
    for short, field in _CAPABILITY_FIELDS.items():
        if capabilities.get(short) is not None:
            out[field] = bool(capabilities[short])
    return out


def _source_info(free: bool | None, session_header: str | None) -> dict[str, object]:
    """线路自己的那几个 model_info 键。``None`` 是「不动」,所以不出现。

    清空用的是空值而不是 null:网关 PATCH 合并 model_info 时丢掉 null
    (``exclude_none``),发 null 等于什么都没改。``""`` 让网关侧的钩子不转会话
    (它只认非空的头名)。会话头只有名字,放在 model_info 里公开无妨。
    """
    out: dict[str, object] = {}
    if free is not None:
        out[FREE] = bool(free)
    if session_header is not None:
        out[SESSION_HEADER] = session_header
    return out


def _unpriced_note(params: object, info: object) -> str | None:
    """无价时给人一句人话:缺的是哪一边,以及为什么会坏事。"""
    if price_is_set(params, info):
        return None
    inp = _price_value((params, info), "input_cost_per_token")
    out = _price_value((params, info), "output_cost_per_token")
    has_in = inp is not None and inp > 0
    has_out = out is not None and out > 0
    if not has_in and not has_out:
        return "网关没给输入或输出单价;无价模型会让项目的 max_budget 刹车静默失效。"
    missing = "输入" if not has_in else "输出"
    present = "输出" if missing == "输入" else "输入"
    return (
        f"网关只给了{present}单价,缺{missing}单价;只定一边会让一半流量按零计费,"
        "同样是刹车失效。"
    )


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _admin_source(row: dict) -> AdminSource:
    info = _dict(row.get("model_info"))
    params = _dict(row.get("litellm_params"))
    upstream = params.get("model")
    upstream = upstream if isinstance(upstream, str) else ""
    api_base = params.get("api_base")
    order = params.get("order", info.get("order"))
    header_names = {
        name
        for source in (params.get(EXTRA_HEADERS), info.get(UPSTREAM_HEADERS))
        if isinstance(source, dict)
        for name in source
        if isinstance(name, str)
    }
    session = info.get(SESSION_HEADER)
    model_id = info.get("id")
    return AdminSource(
        source_id=model_id if isinstance(model_id, str) and model_id else "",
        origin="runtime" if info.get("db_model") is True else "config",
        blocked=info.get("blocked") is True,
        order=order if isinstance(order, int) and not isinstance(order, bool) else None,
        protocol=split_upstream(upstream)[0],
        upstream_model=upstream,
        api_base=api_base if isinstance(api_base, str) and api_base else None,
        prices=_read_prices(params, info),
        free=declared_free(params, info),
        priced=price_is_set(params, info),
        header_names=tuple(sorted(header_names)),
        session_header=session if isinstance(session, str) and session else None,
        catalogue=catalogue_info(info),
        upstream_info={k: v for k, v in info.items() if k in _UPSTREAM_INFO_KEYS},
    )


def _try_order(pair: tuple[int, AdminSource]) -> tuple[bool, int, int]:
    """网关试线路的先后:有 order 的按数从小到大,没有的垫底,同档按列出的先后。"""
    index, source = pair
    return (source.order is None, source.order or 0, index)


def _admin_model(name: str, rows: list[dict]) -> AdminModel:
    """一组同名的行 -> 一个模型。模型层的字段读第一行(目录也读它),能不能用、
    有没有价看整组,口径见 ``AdminModel.sources``。"""
    row = rows[0]
    info = _dict(row.get("model_info"))
    params = _dict(row.get("litellm_params"))
    upstream = params.get("model")
    api_base = params.get("api_base")
    provider = info.get("litellm_provider")
    if not isinstance(provider, str) or not provider:
        # 没给 provider 就从上游名里取前缀(``anthropic/xxx`` -> ``anthropic``)。
        provider = (
            upstream.split("/", 1)[0]
            if isinstance(upstream, str) and "/" in upstream
            else ""
        )
    model_id = info.get("id")
    label = info.get("cheese_label")
    tier = info.get("cheese_tier")
    sources = tuple(
        s for _, s in sorted(enumerate(_admin_source(r) for r in rows), key=_try_order)
    )
    live = [r for r in rows if _dict(r.get("model_info")).get("blocked") is not True]
    unpriced = next(
        (
            r
            for r in (live or rows)
            if not price_is_set(r.get("litellm_params"), _dict(r.get("model_info")))
        ),
        None,
    )
    return AdminModel(
        name=name,
        model_id=model_id if isinstance(model_id, str) and model_id else name,
        label=label if isinstance(label, str) and label else name,
        origin="config" if any(s.origin == "config" for s in sources) else "runtime",
        blocked=not live,
        selectable=info.get("cheese_selectable") is True,
        priced=unpriced is None,
        tier=tier if isinstance(tier, str) and tier else "included",
        overseas=info.get("cheese_overseas") is True,
        upstream_model=upstream if isinstance(upstream, str) else "",
        api_base=api_base if isinstance(api_base, str) and api_base else None,
        provider=provider,
        prices=_read_prices(params, info),
        capabilities=_read_capabilities(info),
        supports_notes=(
            None
            if unpriced is None
            else _unpriced_note(
                unpriced.get("litellm_params"), _dict(unpriced.get("model_info"))
            )
        ),
        sources=sources,
    )


def _admin_key(row: dict) -> AdminKey:
    token = row.get("token")
    alias = row.get("key_alias") or row.get("key_name")
    user_id = row.get("user_id")
    max_budget = row.get("max_budget")
    budget_duration = row.get("budget_duration")
    created_at = row.get("created_at")
    return AdminKey(
        key_hash=token if isinstance(token, str) else "",
        alias=alias if isinstance(alias, str) else "",
        user_id=user_id if isinstance(user_id, str) else None,
        spend=_num(row.get("spend")),
        max_budget=(
            _num(max_budget)
            if isinstance(max_budget, int | float) and not isinstance(max_budget, bool)
            else None
        ),
        budget_duration=budget_duration if isinstance(budget_duration, str) else None,
        blocked=row.get("blocked") is True,
        created_at=created_at if isinstance(created_at, str) else None,
    )
