"""`gateway_models` 的纯函数层：数据转换、用量聚合、价格校验、密钥脱敏、快照构造。

这一层与 `GatewayModelsService` 的异步编排正交：全是无状态函数，不碰数据库、不碰
网关，进出的就是页面契约里的那几种形状 —— 所以单测可以直接调，不必先起一个服务。

名字从 ``app.domain.agent.gateway_models`` 取仍然有效：那边把它们导入进来用，也
按原路径重导出。
"""

import re
from collections.abc import Mapping
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from app.core.errors import BadRequestError
from app.core.sentences import listing, say
from app.domain.agent.gateway_admin import (
    SPEND_CAP,
    AdminKey,
    AdminModel,
    AdminSource,
    ModelUsage,
    UsageWindow,
)
from app.domain.agent.schemas import api_base_allowed, split_upstream, upstream_string

# 契约 §3.3：模型名 1..64 位，只允许这几类字符。
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# 单价与能力在网关管理 API 与页面契约里用同一套语义键；流转成 litellm_params 的
# `*_cost_per_token` 只发生在 `_prices_for_gateway` 一处。
_PRICE_KEYS = ("input", "output", "cache_read", "cache_creation", "cache_creation_1h")
# 审计去敏要认的字段名（大小写、连字符/下划线都归一）——只剔凭据，`api_base` 不动。
# 后三个是 OAuth token 的字段名：出现在审计素材里的这几种同样是凭据。
_KEY_FIELD_NAMES = {"api_key", "apikey", "access_token", "refresh_token", "id_token"}


_ZERO_USAGE = {
    "spend_usd": 0.0,
    "requests": 0,
    "failed_requests": 0,
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 0,
}


def _daily_model_row(name: str, day: dict) -> ModelUsage | None:
    """`usage.daily` 一天里某个模型的那一份；没用过是 None。"""
    by_model = day.get("by_model")
    row = by_model.get(name) if isinstance(by_model, dict) else None
    return row if isinstance(row, ModelUsage) else None


def _daily_tokens(name: str, usage: UsageWindow) -> list[int]:
    """列表项的逐日 token 序列：与 `_series` 同一次窗口、同一个切法。"""
    out: list[int] = []
    for day in usage.daily:
        row = _daily_model_row(name, day)
        out.append(row.total_tokens if row is not None else 0)
    return out


def _usage_to_dict(usage: ModelUsage | None) -> dict:
    """`ModelUsage`（或缺失时的 None）→ 契约里的用量形状。缺的键补 0，不给 null：
    页面上「这个模型这周没被调用」和「这个模型不存在」是两件事，前者是 0。"""
    if usage is None:
        return dict(_ZERO_USAGE)
    return {
        "spend_usd": usage.spend_usd,
        "requests": usage.requests,
        "failed_requests": usage.failed_requests,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "total_tokens": usage.total_tokens,
    }


def _offered_reasons(model: AdminModel) -> list[str]:
    """`offered=false` 时挡住它的**所有**原因，按码给出，管理页按读者的语言说 ——
    只报第一个会让人改完发现还是上不了架。

    ``blocked``：已在网关停用；``unlisted``：网关里没有标 cheese_selectable；
    ``unpriced``：缺输入或输出单价。"""
    reasons: list[str] = []
    if model.blocked:
        reasons.append("blocked")
    if not model.selectable:
        reasons.append("unlisted")
    if not model.priced:
        reasons.append("unpriced")
    return reasons


def _host_of(api_base: str | None) -> str | None:
    if not api_base:
        return None
    try:
        return urlsplit(api_base).netloc or None
    except ValueError:
        return None


def _price_is_positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _require_price_when_selectable(
    selectable: bool, prices: dict | None, free: bool = False
) -> None:
    """上架的模型，这一条线路必须两向有价，或者是声明出来的免费（`_source_prices`
    已经保证免费线路两向都是 0）。"""
    if not selectable or free:
        return
    prices = prices or {}
    if not (
        _price_is_positive(prices.get("input"))
        and _price_is_positive(prices.get("output"))
    ):
        raise BadRequestError(say("offeredNeedsPrices"))


def _offered(model: AdminModel) -> bool:
    """Would the picker offer it (the same test as the page's ``offered``)."""
    return model.selectable and model.priced and not model.blocked


def _require_another_offered(models: list[AdminModel], name: str) -> None:
    """A write that stops ``name`` being offered must leave some model offered:
    with none, every agent settings page is bare."""
    if not any(m.name != name and _offered(m) for m in models):
        raise BadRequestError(say("gatewayLastOfferedModel", name=name))


def _require_prices(prices: dict | None, free: bool) -> None:
    """Every row writes both token prices, offered or not: the platform bills
    usage under a model's name by them (``usage.pricing``), the subscription's
    Claude models included. One direction may be 0 (a transcription model
    charges for audio in and nothing out), not both: that is a free row, and a
    free row says so (``cheese_free``, both written as 0 by ``_source_prices``).
    An offered model is held to more, both > 0 (``_require_price_when_selectable``).
    """
    if free:
        return
    prices = prices or {}
    written = [prices.get("input"), prices.get("output")]
    if any(v is None for v in written) or not any(
        _price_is_positive(v) for v in written
    ):
        raise BadRequestError(say("sourceNeedsPrices"))


def _same_prices(a: dict, b: dict) -> bool:
    """The same prices, to a float's rounding: the page round-trips a rate
    through a per-million text, which is not always the same float back."""
    if a.keys() != b.keys():
        return False
    return all(abs(a[k] - b[k]) <= 1e-9 * max(abs(a[k]), abs(b[k]), 1e-12) for k in a)


def _billable(model: AdminModel) -> bool:
    """Is every serving row's usage billed: free, or both prices written with at
    least one > 0 (the rule ``_require_prices`` holds every write to). Looser than
    ``priced``, which an offered model needs: an unlisted transcription model
    with output priced at 0 is billable without being offerable."""
    live = [s for s in model.sources if not s.blocked] or list(model.sources)
    return bool(live) and all(
        s.free
        or (
            "input" in s.prices
            and "output" in s.prices
            and (s.prices["input"] > 0 or s.prices["output"] > 0)
        )
        for s in live
    )


def _refuse_cleared_prices(current: dict, new: dict) -> None:
    """A price a row has can be changed, never emptied. Emptying one would leave
    whatever reads it charging the token at another rate (an absent cache price
    falls back to the input price) or at nothing."""
    gone = [key for key in _PRICE_KEYS if key in current and new.get(key) is None]
    if gone:
        raise BadRequestError(say("sourcePriceCleared", prices=listing(gone)))


def _source_prices(prices: dict | None, free: bool) -> dict:
    """一条线路要写进网关的单价，按「两向都是零只属于免费」这条规则收一遍。

    免费线路：输入、输出必须是 0，没填就写成 0 —— 写出来的 0 才是价（`declared_free`），
    缺着的会被网关当成没价。不免费的线路：输入、输出都写 0 就拒，那是没说出口的免费。
    一向是 0 可以（语音转写按音频收输入、输出不收钱）；上架的模型另有更严的一条，两向都
    要 > 0（`_require_price_when_selectable`），否则一半流量按零计、刹车失效。缓存那几项
    不在此列：不少上游缓存写入本来就不收钱（MiMo 的 `cache_creation` 就是 0）。
    """
    out = dict(prices or {})
    if free:
        for field in ("input", "output"):
            value = out.get(field)
            if value is None:
                out[field] = 0.0
            elif value != 0:
                raise BadRequestError(say("sourceFreeNeedsZeroPrices"))
    elif out.get("input") == 0 and out.get("output") == 0:
        raise BadRequestError(say("sourceZeroPriceNeedsFree"))
    return out


def _would_lead(model: AdminModel, order: int) -> bool:
    """一条优先级为 `order` 的新线路，会不会接走这个模型在用线路的流量。

    LiteLLM 先在健康的、带 order 的部署里取最小的那一档；没有 order 的只在所有带
    order 的都不可用时才轮到。所以组里在用的线路要么都没有 order（新线路一来就排在
    它们前面），要么新线路的数不大于其中最小的那个（排到最前或与之分流），就是
    「接走流量」。整组都停着时谁也不接走谁。
    """
    live = [s.order for s in model.sources if not s.blocked]
    if not live:
        return False
    ordered = [o for o in live if o is not None]
    return not ordered or order <= min(ordered)


def _translated_to_chat(upstream_model: str) -> bool:
    """这条线路的请求要从 Messages 翻成 OpenAI chat/completions 吗。"""
    return split_upstream(upstream_model)[0] == "openai-chat"


def _api_base_for(upstream_model: str, api_base: str | None) -> str | None:
    """按协议收一遍上游地址。

    原生 Messages 的地址不带 `/v1`：LiteLLM 自己拼 `/v1/messages`，带了就成了
    `/v1/v1/messages`（OpenCode Go 的 Anthropic 入口是 `https://opencode.ai/inference/go`）。
    Chat 线路正相反，地址要带到 `/v1`，那由人填，这里不猜别家的路径。
    """
    if not api_base:
        return api_base
    base = api_base.rstrip("/")
    if split_upstream(upstream_model)[0] == "anthropic" and base.endswith("/v1"):
        base = base[: -len("/v1")]
    return base


def _upstream_of(data: dict) -> str:
    upstream_model = str(data.get("upstream_model") or "").strip()
    if not upstream_model:
        # 上游标识是这条线路唯一「打到哪儿去」的答案，缺了它网关只会建出一条
        # 谁调谁失败的部署 —— 那不是线路，是一条陷阱。
        raise BadRequestError(say("upstreamModelRequired"))
    return upstream_string(data.get("protocol"), upstream_model)


def _bare_upstream(source: AdminSource) -> str:
    """线路的上游模型名，去掉协议前缀（认不出前缀就是整串）。"""
    return split_upstream(source.upstream_model)[1]


def _refuse_config_source(model: AdminModel, source: AdminSource) -> None:
    if source.origin == "config":
        raise BadRequestError(say("sourceConfigReadOnly", name=model.name))


def _source_target(name: str, source_id: str) -> str:
    return f"{name}/{source_id}"[:128]


def _spend_cap(source: AdminSource) -> float | None:
    """The row's spend cap as the gateway enforces it: a positive number, or none
    (``spend_cap.declared_cap`` reads 0, a negative or a non-number as no cap)."""
    cap = source.upstream_info.get(SPEND_CAP)
    if isinstance(cap, bool) or not isinstance(cap, int | float) or cap <= 0:
        return None
    return float(cap)


# 消费上限是终身累计（spend_cap.py 不分窗口求和），所以读「已用」的窗口从网关还不
# 存在的日子开始：账里不会有比它更早的一天。
LEDGER_START = date(2000, 1, 1)


def _spend_cap_used(
    source: AdminSource, ledger: Mapping[str, ModelUsage] | None
) -> float | None:
    """这条线路的上游模型累计花了多少，口径与 spend_cap.py 拦截时一样：网关账里
    这个上游模型名下的全部花费，不分窗口、不分模型名。没设上限、或没读那份账时是
    None。"""
    if _spend_cap(source) is None or ledger is None:
        return None
    used = ledger.get(source.upstream_model)
    return used.spend_usd if used is not None else 0.0


def _source_item(
    source: AdminSource, ledger: Mapping[str, ModelUsage] | None = None
) -> dict:
    """页面上的一条线路。上游凭据网关本就不给；请求头只给名字不给值。
    ``ledger`` 是设了上限的上游各自从 `LEDGER_START` 起的终身账
    （`answers._cap_spend`），给「上限已用多少」。"""
    return {
        "source_id": source.source_id,
        "origin": source.origin,
        "blocked": source.blocked,
        "order": source.order,
        "protocol": source.protocol,
        "upstream_model": _bare_upstream(source),
        "upstream_raw": source.upstream_model,
        "host": _host_of(source.api_base),
        "prices": dict(source.prices),
        "free": source.free,
        "priced": source.priced,
        "header_names": list(source.header_names),
        "session_header": source.session_header,
        "spend_cap_usd": _spend_cap(source),
        "spend_cap_used_usd": _spend_cap_used(source, ledger),
    }


def _source_snapshot(source: AdminSource) -> dict:
    """审计的 `before`：一条线路写前的样子。同 `_source_item`，没有凭据也没有头的值。"""
    return {
        "source_id": source.source_id,
        "origin": source.origin,
        "blocked": source.blocked,
        "order": source.order,
        "upstream_model": source.upstream_model,
        "api_base": source.api_base,
        "prices": dict(source.prices),
        "free": source.free,
        "header_names": list(source.header_names),
        "session_header": source.session_header,
        "spend_cap_usd": _spend_cap(source),
    }


def _prices_for_gateway(prices: dict | None) -> dict:
    """页面契约的单价（null 表示「没有这一项」）→ 只留真正给了值的键交给客户端；
    不把 null 当 0 传下去 —— 那会把「没定价」写成「免费」。"""
    prices = prices or {}
    return {
        key: float(prices[key]) for key in _PRICE_KEYS if prices.get(key) is not None
    }


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise BadRequestError(say("modelNameInvalid"))


def _validate_api_base(api_base: str | None) -> None:
    if api_base is None:
        return
    if not api_base_allowed(api_base):
        raise BadRequestError(say("apiBaseNeedsHttps"))


def _refuse_config_write(model: AdminModel, *, toggle: bool = False) -> None:
    if model.origin != "config":
        return
    verb = say("configVerbToggle" if toggle else "configVerbEdit")
    raise BadRequestError(say("configModelReadOnly", name=model.name, verb=verb))


def _is_key_field(field: object) -> bool:
    return str(field).strip().lower().replace("-", "_") in _KEY_FIELD_NAMES


def _redact(value: Any) -> Any:
    """递归剔掉任何 `api_key` 字段 —— 审计素材里绝不留上游凭据（契约 §2.2）。

    线路的静态请求头只留名字：认证头虽然不让按线路写，头的值仍可能是哪家中转站
    认人的东西，页面上列线路时就只给名字，审计不该是读到它的后门。
    """
    if isinstance(value, dict):
        return {
            k: sorted(v)
            if k == "upstream_headers" and isinstance(v, dict)
            else _redact(v)
            for k, v in value.items()
            if not _is_key_field(k)
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v) for v in value]
    return value


def _secrets_in(value: Any) -> set[str]:
    """素材里出现过的 key 值，用来把错误消息里可能带出的同一串也抹掉。"""
    found: set[str] = set()
    if isinstance(value, dict):
        for k, v in value.items():
            if _is_key_field(k):
                if isinstance(v, str) and v:
                    found.add(v)
            else:
                found |= _secrets_in(v)
    elif isinstance(value, (list, tuple)):
        for item in value:
            found |= _secrets_in(item)
    return found


def _scrub(text: str | None, secrets: set[str]) -> str | None:
    if text is None:
        return None
    for secret in secrets:
        text = text.replace(secret, "***")
    return text


def _config_yaml(model: AdminModel) -> str:
    """config 模型的「怎么改」片段：照网关 config.yaml 的形状给出可粘贴的几行。

    上游凭据在网关侧已被剥掉，这里只能给一个占位的环境变量名 —— 那也正好，凭据
    本来就不该从页面读出来。
    """
    lines = [
        f"- model_name: {model.name}",
        "  litellm_params:",
        f"    model: {model.upstream_model}",
    ]
    if model.api_base:
        lines.append(f"    api_base: {model.api_base}")
    lines.append("    api_key: os.environ/REPLACE_WITH_ENV_VAR")
    fields = {
        "input": "input_cost_per_token",
        "output": "output_cost_per_token",
        "cache_read": "cache_read_input_token_cost",
        "cache_creation": "cache_creation_input_token_cost",
        "cache_creation_1h": "cache_creation_input_token_cost_above_1hr",
    }
    for key, field in fields.items():
        if key in model.prices:
            lines.append(f"    {field}: {model.prices[key]}")
    lines.append("  model_info:")
    lines.append(f"    cheese_selectable: {'true' if model.selectable else 'false'}")
    lines.append(f"    cheese_label: {model.label}")
    lines.append(f"    cheese_tier: {model.tier}")
    if model.overseas:
        lines.append("    cheese_overseas: true")
    for capability, enabled in model.capabilities.items():
        if enabled:
            lines.append(f"    supports_{capability}: true")
        elif capability == "mid_conversation_system":
            # 别的能力缺省就是没有；这一项缺省是照常发，只有写明 false 才算数。
            lines.append(f"    supports_{capability}: false")
    return "\n".join(lines) + "\n"


def _model_snapshot(model: AdminModel) -> dict:
    """审计的 `before`：这条模型写前的样子，用来回答「原来是什么」。

    只挑**非凭据**字段，并在 `_record` 里再过一遍 `_redact`：审计素材里出现上游 key
    等于把这条模型连同它的额度送人。`AdminModel` 本就从网关剥过凭据，这里是纵深防御。
    """
    return {
        "name": model.name,
        "label": model.label,
        "origin": model.origin,
        "blocked": model.blocked,
        "selectable": model.selectable,
        "priced": model.priced,
        "tier": model.tier,
        "overseas": model.overseas,
        "upstream_model": model.upstream_model,
        "api_base": model.api_base,
        "provider": model.provider,
        "prices": dict(model.prices),
        "capabilities": dict(model.capabilities),
        "sources": [_source_snapshot(source) for source in model.sources],
    }


def _key_snapshot(key: AdminKey) -> dict:
    """审计的 `before`：这把项目密钥写前的预算状态。不涉凭据，只有额度与归属。"""
    return {
        "key_alias": key.alias,
        "user_id": key.user_id,
        "spend": key.spend,
        "max_budget": key.max_budget,
        "budget_duration": key.budget_duration,
        "blocked": key.blocked,
    }


def _item_from_payload(
    name: str, data: dict, model_id: str, usage: ModelUsage | None = None
) -> dict:
    """网关刚写完、读回还没反映出这一条时的兜底项（见 `_read_back`）。

    刻意只填能确定的部分：单价、能力、上游地址都来自刚提交的内容，`usage` 全 0。
    下一次刷新会换成网关给的真值。
    """
    selectable = bool(data.get("selectable"))
    prices = _prices_for_gateway(data.get("prices"))
    priced = "input" in prices and "output" in prices
    return {
        "name": name,
        "model_id": model_id,
        "label": data.get("label") or name,
        "origin": "runtime",
        "blocked": False,
        "selectable": selectable,
        "priced": priced,
        "tier": data.get("tier") or "included",
        "overseas": bool(data.get("overseas")),
        "offered": selectable and priced,
        "roles": [],
        # 网关还没读回这条新模型：`pending`。
        "blocked_reasons": [] if (selectable and priced) else ["pending"],
        "unpriced_reason": None if priced else "网关尚未读回单价",
        "upstream": {
            "model": data.get("upstream_model"),
            "host": _host_of(data.get("api_base")),
            "provider": "",
        },
        "prices": prices,
        "capabilities": dict(data.get("capabilities") or {}),
        "usage": _usage_to_dict(usage),
        "series": [],
        "sources": [],
    }
