"""`gateway_admin` 的形状与线路键：网关把东西存在哪几个键里，我们把它读成什么。

三个文件的叶子。只认识 stdlib 与网关那套键名，不认识上面两层（谁在读、读来干什么），
所以解析层和客户端都能往这里取名字。

**为什么单独一个文件。** 解析层要造的形状（``ModelUsage``、``AdminSource``、
``AdminModel``、``AdminKey``）与要读的线路键（``FREE``、``EXTRA_HEADERS``……）原来是
``gateway_admin`` 里的。留在那儿，解析层就得反过来导入 ``gateway_admin``，与
``gateway_admin -> gateway_admin_parsers`` 凑成一个环；``app/domain/agent`` 里的模块
之间不许成环（``.importlinter`` 的 C3，``scripts/check_boundaries.py`` 拦得住）。
名字下沉一层，方向就只剩一条直线：types <- parsers <- ``gateway_admin``。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

# 一条线路自己的 model_info 键：不跟着模型走，加新线路时也不从别的线路抄。名字与网关
# 侧读它们的钩子（deploy/gateway 的 upstream_headers.py）逐字一致。
#
# 静态请求头不写进 model_info，写进 ``litellm_params.extra_headers``：LiteLLM 原样把
# 它发给上游（两种协议都实测过），而 ``/model/info`` 对每把虚拟 key 都开放，
# model_info 原样回、litellm_params 过 LiteLLM 的掩码（名字里带 key/token/secret/
# auth/password/credential/cookie 一段的头，值被打码）。所以页面只列头名，头的值
# 按「名字不像凭据就是公开的」对待，凭据走 ``api_key``。config.yaml 里写在
# model_info 的那份（``cheese_upstream_headers``）照样读出头名。
EXTRA_HEADERS = "extra_headers"
UPSTREAM_HEADERS = "cheese_upstream_headers"
SESSION_HEADER = "cheese_session_header"
FREE = "cheese_free"
# What one upstream model needs (no_explicit_cache.py, spend_cap.py), not
# the model as people pick it: a new source on the same upstream model takes
# them from a row that has them, one on another upstream does not.
NO_EXPLICIT_CACHE = "cheese_no_explicit_cache"
SPEND_CAP = "cheese_spend_cap_usd"
_UPSTREAM_INFO_KEYS = frozenset({NO_EXPLICIT_CACHE, SPEND_CAP})
# Whether this deployment runs Anthropic's server web search
# (deploy/gateway/web_search.py). It describes one server, not the model or
# its upstream string: RUC's vLLM and a DeepSeek relay both call
# anthropic/deepseek-flash, and only one of them searches. A new source never
# takes it from another row; it is marked after it has been measured.
WEB_SEARCH = "supports_web_search"
_SOURCE_INFO_KEYS = frozenset({UPSTREAM_HEADERS, SESSION_HEADER, FREE, WEB_SEARCH}) | (
    _UPSTREAM_INFO_KEYS
)


# 我方对外用短名,网关用 LiteLLM 的字段名。翻译只在这一处发生,别让字段名散到服务层
# 和前端去——那样改一个名字要同时改三处,漏一处就是一处静默对不上。
_PRICE_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_creation": "cache_creation_input_token_cost",
    # The one-hour cache write, which Anthropic prices apart from the 5-minute one;
    # subscription billing reads it (``usage.pricing.Rate.cache_write_1h``).
    "cache_creation_1h": "cache_creation_input_token_cost_above_1hr",
}

# 能力位同理:网关用 supports_*/adaptive 一整套名字,页面只关心这几件。
_CAPABILITY_FIELDS = {
    "reasoning": "supports_reasoning",
    "vision": "supports_vision",
    "adaptive_thinking": "supports_adaptive_thinking",
    "mid_conversation_system": "supports_mid_conversation_system",
}


def catalogue_info(info: Mapping[str, object]) -> dict[str, object]:
    """一行 model_info 里属于**模型**、同组每一行必须一字不差的那部分。

    目录读 ``/model/info`` 列出的第一行（上架、名字、档位、思考档、能力都从那一行来），
    哪一行排第一不由我们定，所以同组每一行都得带同一份。线路自己的键
    (``_SOURCE_INFO_KEYS``)与网关自己写进去的键(``id``、``db_model``、``blocked``……)
    都不在内 —— 抄了 ``id`` 就是两行同一个部署 id,一行 429 冷却全组(#3294)。
    """
    return {
        key: value
        for key, value in info.items()
        if (key.startswith("cheese_") and key not in _SOURCE_INFO_KEYS)
        # 每一个显式写了的 supports_* 都抄：`supports_output_config`、
        # `supports_max_reasoning_effort` 决定思考档能不能送到上游，少抄一个，
        # 落到新线路的请求就悄悄丢了 effort。没写（None）的是网关自己的默认，不抄。
        or (
            key.startswith("supports_")
            and key not in _SOURCE_INFO_KEYS
            and isinstance(value, bool)
        )
    }


@dataclass(frozen=True)
class AdminSource:
    """一个模型下的一条线路 —— 网关里的一条部署(``/model/info`` 的一行)。

    ``order`` 是 LiteLLM 的优先级(小的先试,相同的一起分流,没有的垫底)。
    ``header_names`` 只有头名:值可能是凭据一类的东西,页面不该读到。
    ``catalogue`` 是这一行里属于模型的那份元数据(``catalogue_info``),给「加线路」抄用。
    """

    source_id: str
    origin: str  # "config" | "runtime"
    blocked: bool
    order: int | None
    protocol: str | None  # None = 认不出的前缀,``upstream_model`` 是完整模型串
    upstream_model: str  # LiteLLM 的完整模型串
    api_base: str | None
    prices: dict[str, float]
    free: bool
    priced: bool
    header_names: tuple[str, ...]
    session_header: str | None
    catalogue: dict[str, object]
    # This row's keys for its upstream model (``_UPSTREAM_INFO_KEYS``).
    upstream_info: dict[str, object]


@dataclass(frozen=True)
class AdminModel:
    """管理页眼里的一条模型。

    ``origin`` 决定它可不可改:``config`` 来自 config.yaml,页面只读;``runtime`` 是
    经管理 API 加进库的,可改可删。

    ``selectable`` 是**存储里的原始标记**(网关的 ``cheese_selectable``),不含
    ``blocked`` 折算;停用与否单独放在 ``blocked`` 里,两者各存各的。能不能真的被
    选到(``offered = selectable && priced && !blocked``)由服务层合起来算——客户端
    这一层只如实搬运网关的字段,不替上层做判断,免得两处各算一遍算出两个答案。
    """

    name: str
    model_id: str
    label: str
    origin: str  # "config" | "runtime"
    blocked: bool
    selectable: bool
    priced: bool
    # 档位（网关的 ``cheese_tier``）,缺省 ``included``:方案按它限定可用的模型。
    tier: str
    # 需要平台管理员给团队开通才能用(网关的 ``cheese_overseas``):Claude、GPT、Gemini。
    overseas: bool
    upstream_model: str
    api_base: str | None
    provider: str
    prices: dict[str, float]  # 键见 _PRICE_FIELDS,缺的键不出现
    capabilities: dict[str, bool]  # 键见 _CAPABILITY_FIELDS
    supports_notes: str | None  # 缺价的原因等人话,priced=False 时给出
    # 这个模型的全部线路,按网关试它们的先后排好。模型那一层的字段读自
    # ``/model/info`` 列出的第一行(目录也读那一行);``origin`` 有一行来自
    # config.yaml 就是 ``config``,``blocked`` 要每一行都停了才算,``priced`` 要每条
    # 在用的线路都有价 —— 与 ``LlmGateway.models`` 给选择器的答案同一个口径。
    sources: tuple[AdminSource, ...] = ()


@dataclass(frozen=True)
class AdminKey:
    """一条虚拟 key。项目额度(刹车)就落在它的 ``max_budget`` 上。"""

    key_hash: str
    alias: str
    user_id: str | None
    spend: float
    max_budget: float | None
    budget_duration: str | None
    blocked: bool
    created_at: str | None


@dataclass(frozen=True)
class ModelUsage:
    """一个模型(或整平台)在一个窗口里的一条用量。"""

    spend_usd: float
    requests: int
    failed_requests: int
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class UsageWindow:
    """一段闭区间里的用量,平台级、按模型、按项目各切一份。"""

    start_date: str
    end_date: str
    totals: ModelUsage  # 平台级
    by_model: dict[str, ModelUsage]  # model_group -> 用量
    by_key: dict[str, ModelUsage]  # key hash -> 用量
    daily: list[dict]  # [{date, totals: ModelUsage, by_model: {name: ModelUsage}}]
    # 上游模型（``litellm_params.model``）-> 用量：``breakdown.models``，键就是
    # ``LiteLLM_DailyUserSpend.model``，spend_cap.py 拦截时求和的正是这一列。
    by_upstream: dict[str, ModelUsage] = field(default_factory=dict)
