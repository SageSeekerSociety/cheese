"""网关模型管理页的请求体。

这个页面上有几件事会被提交：新建或编辑一个模型、给它加或改一条线路（同名下的一条
部署）、调线路的优先级、停用或启用、改一个项目的刹车值。每件事一个模型，字段名
与 `frontend/src/api.ts` 那份类型接口一一对应 —— 后端是这段契约的权威，前端照着
它写（同 `admin/schemas.py` 的 `AdminAdd`）。

这里只管**形状**：字段名、类型、名字的字符集与长度、上游地址必须是 https（唯一例外
见 `api_base_allowed`）、请求头的名字与值。业务上的判断
一个都不放：`selectable=true` 却缺输入或输出单价要由服务层拒（那句「无价模型会让项目的
max_budget 刹车静默失效」得和网关目录刷新、审计落库一起说，放进 pydantic 只会让同一句话
有第二份、让路由层也当一次裁判），而「config 来源不许写」「这条模型不存在」都要读网关或
库才答得上来，同样在服务层。路由与服务合起来才是契约 §2.3 那一张失败语义表。
"""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from app.core.sentences import say

# 模型名允许的字符集。它同时是网关路由里的 key 和页面上唯一的标识，放空格或斜杠进来
# 只会让它在 URL 与日志里被编成一团，没有好处。长度跟着契约的 1..64。
_NAME_PATTERN = r"^[A-Za-z0-9._-]+$"


class ModelPrices(BaseModel):
    """一条模型的单价。

    用 `None` 表示「没定」，不是 0：0 是「定成免费」，`None` 是「这一栏空着」，网关的写
    接口也按这个区分（`GatewayAdmin._price_params` 只传真数字，`None` 一个都不发）。
    cache_read / cache_creation 允许缺，是因为不少上游根本没给缓存计价。
    """

    input: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    output: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_read: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    cache_creation: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    # The one-hour cache write (`cache_creation_input_token_cost_above_1hr`).
    cache_creation_1h: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class ModelCapabilities(BaseModel):
    """能勾的能力。页面认这几项；网关那套 `supports_*` 字段名不出这个包。

    `adaptive_thinking` 必须**声明在这里**，不能指望 pydantic 把它顺手留下：网关侧
    的 `supports_adaptive_thinking` 读写都认它（`gateway_admin` 的读路径会把它读回来
    显示），但一个没声明的键会被 pydantic 默认丢弃 —— 前端勾上、保存后请求体里根本
    没有它，写网关时什么都不会发生，而读回来却显示得出来。那是一个静默的空操作。
    """

    reasoning: bool = False
    vision: bool = False
    adaptive_thinking: bool = False
    # 能不能收 Claude Code 插在对话中间的 system 消息。没传就不写：没人量过的模型
    # 不该被一次保存记成「不支持」。
    mid_conversation_system: bool | None = None


# 计量代理的 ChatGPT 入口：唯一允许走明文 http 的上游地址，路径是 `/chatgpt/<账号名>`
# （账号名的字符集同 deploy/metering-proxy 的 `_ACCOUNT_NAME_RE`）。
_METER_CHATGPT_NETLOC = "metering-proxy:8445"
_METER_CHATGPT_PATH = re.compile(r"/chatgpt/[A-Za-z0-9][A-Za-z0-9._-]{0,63}/?")


def api_base_allowed(value: str) -> bool:
    """这个上游地址能不能填：https，或者恰好是计量代理的 ChatGPT 入口。

    那一个 http 地址是安全的：`metering-proxy` 只在网关与计量代理两家独占的内部
    docker 网络上解析得到（deploy/metering-proxy/compose.yml），这一跳不出那张网；
    而这条模型带过去的 api_key 是那个入口的钥匙（CHEESE_CHATGPT_KEY），不是任何
    上游凭据 —— 账号的 token 由计量代理在那一头换上。主机、端口、路径都逐字比，
    `metering-proxy.evil`、别的端口、带 userinfo 的写法都不算。
    """
    if value.startswith("https://"):
        return True
    parts = urlsplit(value)
    return (
        value.startswith("http://")
        and parts.netloc == _METER_CHATGPT_NETLOC
        and _METER_CHATGPT_PATH.fullmatch(parts.path) is not None
        and not parts.query
        and not parts.fragment
    )


def _clean_api_base(value: str | None) -> str | None:
    """上游地址的唯一一条形状规则，模型体与线路体共用。"""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not api_base_allowed(value):
        raise ValueError(say("apiBaseMustBeHttps"))
    return value


# ---- 线路（同一个模型名下的一条部署）----
#
# 一个模型可以有好几条线路：官方 API、OpenCode Go 订阅、免费模型、国内中转站，网关按
# `order` 从小到大试。这里只管一条线路的**形状**；「免费必须两向都写 0」「上架的模型每个
# 线路都得有价」这类要看合并后状态或看同组别的线路才答得上的判断，同样在服务层。

# 协议 → 上游模型串的前缀。页面只让人选协议，不让人记 LiteLLM 的前缀：
#
# - `anthropic`：上游说原生 Messages，原样转过去（GLM、Kimi、MiMo 都是这条）。
# - `openai-chat`：上游只有 OpenAI 兼容的 chat/completions。前缀不能是 `openai/` ——
#   调用方说 Anthropic 协议，LiteLLM 会把 `openai/` 前缀的 Messages 请求翻成
#   Responses API（`_RESPONSES_API_PROVIDERS = {"openai"}`），而中转站和 OpenCode Go
#   多半只有 chat/completions。`hosted_vllm/` 让 Messages 请求走 chat/completions 的
#   翻译，并且保留 reasoning effort、以 LiteLLM 自己的 HTTP 客户端发出；`custom_openai/`
#   这类走 OpenAI SDK 的提供方丢 effort、改 User-Agent。它会删掉历史 assistant 消息的
#   `reasoning_content`，网关用 `deploy/gateway/patch_hosted_vllm_reasoning.py` 留住。
# - `openai-responses`：上游有 Responses API（计量代理的 ChatGPT 入口就是这条）。
OPENAI_CHAT_PREFIX = "hosted_vllm"
PROTOCOL_PREFIXES: dict[str, str] = {
    "anthropic": "anthropic",
    "openai-chat": OPENAI_CHAT_PREFIX,
    "openai-responses": "openai",
}
Protocol = Literal["anthropic", "openai-chat", "openai-responses"]


def upstream_string(protocol: str | None, upstream_model: str) -> str:
    """协议 + 上游模型名 → LiteLLM 的模型串；没给协议时原样就是模型串。"""
    if protocol is None:
        return upstream_model
    return f"{PROTOCOL_PREFIXES[protocol]}/{upstream_model}"


def split_upstream(model: str) -> tuple[str | None, str]:
    """`upstream_string` 的反方向：认得出前缀就给 (协议, 上游模型名)，认不出给
    (None, 原串) —— 页面把后者原样显示成「其他」。"""
    prefix, _, rest = model.partition("/")
    for protocol, known in PROTOCOL_PREFIXES.items():
        if prefix == known and rest:
            return protocol, rest
    return None, model


# 优先级（LiteLLM 的 `order`）的取值范围：小的先试，相同的一起分流。
ORDER_MIN = 1
ORDER_MAX = 100

# 线路请求头。名字按 RFC 9110 的 token 字符集收；值不许带控制字符（CR/LF 能把一个头
# 劈成两个，往上游请求里塞进任意头）。上限是给人填的静态值，不是给人贴整段文件的。
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,64}$")
_HEADER_VALUE_MAX = 512
_HEADERS_MAX = 16
# 这几个头不许按线路覆盖：认证头是网关替上游凭据说话的地方，允许在这里写就等于让
# 凭据绕开 `api_key` 的「只写、不回显」，落进一个会被列出名字、写进审计的字段；
# 传输层的几个头改了会让请求本身坏掉；`x-claude-code-session-id` 是调用方带来的会话，
# 往上游转它的正路是 `cheese_session_header`。
_RESERVED_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
    "anthropic-api-key",
    "cookie",
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "x-claude-code-session-id",
}


def _clean_header_name(name: str) -> str:
    name = (name or "").strip()
    if not _HEADER_NAME.fullmatch(name):
        raise ValueError(say("sourceHeaderNameInvalid", name=name[:80]))
    if name.lower() in _RESERVED_HEADERS:
        raise ValueError(say("sourceHeaderReserved", name=name))
    return name


def _clean_headers(value: dict[str, str] | None) -> dict[str, str] | None:
    if value is None:
        return None
    if len(value) > _HEADERS_MAX:
        raise ValueError(say("sourceHeadersTooMany", max=_HEADERS_MAX))
    out: dict[str, str] = {}
    seen: set[str] = set()
    for raw_name, header_value in value.items():
        name = _clean_header_name(raw_name)
        if len(header_value) > _HEADER_VALUE_MAX or any(
            ord(c) < 0x20 or ord(c) == 0x7F for c in header_value
        ):
            raise ValueError(
                say("sourceHeaderValueInvalid", name=name, max=_HEADER_VALUE_MAX)
            )
        if name.lower() in seen:
            raise ValueError(say("sourceHeaderDuplicate", name=name))
        seen.add(name.lower())
        out[name] = header_value
    return out


def _clean_session_header(value: str | None) -> str | None:
    """空串是「不转会话」，与 `None`（这次别碰）是两件事。"""
    if value is None:
        return None
    if not value.strip():
        return ""
    return _clean_header_name(value)


class _SourceFields(BaseModel):
    """一条线路独有的那几项，新建模型、加线路、改线路三处共用。

    `protocol` 给了，`upstream_model` 就是上游自己的模型名（`kimi-k3`），服务层拼上
    前缀；没给，`upstream_model` 就是 LiteLLM 的完整模型串（`deepseek/deepseek-chat`），
    这是三种协议之外的提供方的写法。

    `upstream_headers` 整份替换：给了就是这条线路的全部静态头（`{}` 清空），不给就不动
    —— 页面只列头名、不回显值，「没给」必须是「别碰」。`session_header` 是接收
    Claude Code 会话 id 的头名（如 `x-opencode-session`）。
    """

    protocol: Protocol | None = None
    free: bool | None = None
    upstream_headers: dict[str, str] | None = None
    session_header: str | None = None

    @field_validator("upstream_headers")
    @classmethod
    def _headers_ok(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _clean_headers(value)

    @field_validator("session_header")
    @classmethod
    def _session_header_ok(cls, value: str | None) -> str | None:
        return _clean_session_header(value)


class _ModelBodyBase(BaseModel):
    """新建与编辑共用的那部分形状。

    两处真正共用的一条规则：上游地址必须 https。明文 http 会让上游凭据在路上裸奔，这里
    直接不给人填错的机会（计量代理的 ChatGPT 入口是唯一例外，见 `api_base_allowed`）；
    留空表示「用上游默认端点」，所以空串一并按「没填」处理，免得前端把「没填」写成
    空串时被当成一个非法地址拒掉。
    """

    api_base: str | None = None
    api_key: str | None = None
    api_key_unchanged: bool = False
    label: str | None = None
    prices: ModelPrices | None = None
    capabilities: ModelCapabilities | None = None
    # 档位（网关的 `cheese_tier`）：方案按它限定可用的模型（#2397）。
    tier: Literal["included", "premium", "frontier"] | None = None
    # 只有平台管理员开通过的团队能用（网关的 `cheese_overseas`）：Claude、GPT、Gemini。
    overseas: bool | None = None

    @field_validator("api_base")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        return _clean_api_base(value)


class ModelCreate(_SourceFields, _ModelBodyBase):
    """`POST /admin/gateway/models` 的体 —— 新建一个运行时模型，连同它的第一条线路。

    名字按 `_NAME_PATTERN` 收；`api_key_unchanged` 在新建时没有意义（还没有凭据可保），
    留着是为了让新建与编辑共用同一个体，前端不必为两处写两份形状。
    """

    name: str = Field(min_length=1, max_length=64, pattern=_NAME_PATTERN)
    upstream_model: str = Field(min_length=1, max_length=200)
    selectable: bool = False
    order: int | None = Field(default=None, ge=ORDER_MIN, le=ORDER_MAX)


class ModelUpdate(_ModelBodyBase):
    """`PATCH /admin/gateway/models/{name}` 的体 —— 只传要改的，缺的都不动。

    每个字段都可有可无，`None` 一律表示「这次别碰它」。两处例外，都在签名上：

    - `api_key_unchanged` 默认 `true`：编辑界面不回显上游凭据，所以一次没带 key 的提交
      绝不能把现有 key 清空 —— 那会让这条模型下一次调用直接失败。
    - `name` 虽然在体里（前端把同一个表单原样提交），但**改名不支持**：真正的名字以
      路径上的 `{name}` 为准，服务层的 `update` 也是分开收这两个的。

    上游那几项（`upstream_model` / `api_base` / `api_key` / `prices`）只对只有一个
    线路的模型有意义；有好几条线路时改哪一个说不清，服务层拒绝，改线路走线路的接口。
    """

    name: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=_NAME_PATTERN
    )
    upstream_model: str | None = Field(default=None, min_length=1, max_length=200)
    api_key_unchanged: bool = True
    selectable: bool | None = None


class SourceCreate(_SourceFields):
    """`POST /admin/gateway/models/{name}/sources` 的体 —— 给已有模型加一条线路。

    模型的展示元数据（上架、名字、档位、思考档、能力）不在这里：同组每一行必须一字
    不差（目录读 `/model/info` 列出的第一行），服务层从已有那一行抄过来。`order` 必填：
    LiteLLM 只在所有带 order 的线路都冷却时才轮到不带 order 的，一条忘了填优先级的
    线路等于永远不上场。
    """

    upstream_model: str = Field(min_length=1, max_length=200)
    order: int = Field(ge=ORDER_MIN, le=ORDER_MAX)
    # 这个优先级会让新线路排到所有在用线路的最前（或旁边那几条根本没有 order）时，
    # 必须显式说「就让它当首选」。加线路默认是加一条兜底，不能悄悄接走全部流量。
    make_primary: bool = False
    api_base: str | None = None
    api_key: str | None = None
    prices: ModelPrices | None = None

    @field_validator("api_base")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        return _clean_api_base(value)


class SourceUpdate(_SourceFields):
    """`PATCH /admin/gateway/models/{name}/sources/{source_id}` 的体，缺的都不动。

    `api_key_unchanged` 与 `ModelUpdate` 同理默认 `true`：编辑界面不回显凭据。
    改 `upstream_model` 而不给 `protocol`，按完整模型串收（同 `_SourceFields`）。
    """

    upstream_model: str | None = Field(default=None, min_length=1, max_length=200)
    order: int | None = Field(default=None, ge=ORDER_MIN, le=ORDER_MAX)
    api_base: str | None = None
    api_key: str | None = None
    api_key_unchanged: bool = True
    prices: ModelPrices | None = None
    # The upstream model's lifetime spend cap in USD (deploy/gateway/spend_cap.py).
    # Absent leaves it alone, null clears it. The gateway counts the cap against
    # everything its upstream model has been charged, so the service writes it on
    # every runtime source on that upstream, not on this one alone.
    spend_cap_usd: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @field_validator("api_base")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        return _clean_api_base(value)


class SourceOrder(BaseModel):
    """`PUT /admin/gateway/models/{name}/sources/order` 的体：一次改几条线路的优先级。

    一次写、一行审计、一次目录刷新 —— 「把这条线路挪到最前」往往要动两三条。
    """

    orders: dict[str, int] = Field(min_length=1, max_length=64)

    @field_validator("orders")
    @classmethod
    def _orders_in_range(cls, value: dict[str, int]) -> dict[str, int]:
        if any(not ORDER_MIN <= order <= ORDER_MAX for order in value.values()):
            raise ValueError(say("sourceOrderRange", min=ORDER_MIN, max=ORDER_MAX))
        return value


class BudgetUpdate(BaseModel):
    """`PUT /admin/gateway/projects/{project_id}/budget` 的体。

    `None` 是「清掉这道刹车」，与「填一个 0」是两件事：0 会让项目一步也走不动，清空才是
    回到不限。所以这一栏必须显式给（缺字段直接 400），不能让「忘了传」被悄悄读成清空。
    """

    max_budget_usd: float | None


class RoleUpdate(BaseModel):
    """`PUT /admin/gateway/roles/{role}` 的体：`model` 为 null 是回到部署的默认值
    （`Settings` 里那一项）。必须显式给，缺字段 400，免得「忘了传」被读成「清掉」。"""

    model: str | None = Field(min_length=1, max_length=200)


class BlockedUpdate(BaseModel):
    """`POST /admin/gateway/models/{name}/blocked` 的体。"""

    blocked: bool


# ---- 响应：线路那几条写接口回的形状 ----
#
# 只写进 OpenAPI（`typed_response`），给前端生成类型用；路由照旧回服务层拼的 dict。
# 字段照 `GatewayModelsService._item` / `_source_item`，那两处是权威。


class GatewaySourceOut(BaseModel):
    """一条线路。上游凭据与请求头的值都不在这里。"""

    source_id: str
    origin: Literal["config", "runtime"]
    blocked: bool
    order: int | None
    protocol: Protocol | None
    upstream_model: str
    upstream_raw: str
    host: str | None
    prices: dict[str, float]
    free: bool
    priced: bool
    header_names: list[str]
    session_header: str | None
    spend_cap_usd: float | None
    # 上游模型累计已花多少，口径同 spend_cap.py（终身、按上游模型名）；没设上限是 None。
    spend_cap_used_usd: float | None = None


class GatewayUpstreamOut(BaseModel):
    model: str | None
    host: str | None
    provider: str


class GatewayUsageOut(BaseModel):
    spend_usd: float
    requests: int
    failed_requests: int
    prompt_tokens: int
    completion_tokens: int
    cache_read_tokens: int
    total_tokens: int


class GatewayModelItemOut(BaseModel):
    """模型表里的一项，连同它按先后排好的全部线路。"""

    name: str
    model_id: str
    label: str
    origin: Literal["config", "runtime"]
    blocked: bool
    selectable: bool
    priced: bool
    tier: str
    offered: bool
    # The platform model roles that run on it (`app.core.model_roles`); while
    # any does, it cannot be paused or deleted.
    roles: list[str]
    blocked_reasons: list[str]
    unpriced_reason: str | None
    upstream: GatewayUpstreamOut
    prices: dict[str, float]
    capabilities: dict[str, bool]
    usage: GatewayUsageOut
    series: list[int]
    sources: list[GatewaySourceOut]
    config_yaml: str | None = None


class PlatformRoleOut(BaseModel):
    role: str
    model: str | None


class PlatformRoleItemOut(PlatformRoleOut):
    """`fallback`：没人指过时读的 `Settings` 那一项；`chosen`：有人在页面上指过。
    `offered_only`：只能指到上架的模型（项目默认会被提供给人选）。"""

    fallback: str | None
    chosen: bool
    offered_only: bool


class PlatformRolesOut(BaseModel):
    roles: list[PlatformRoleItemOut]


class GatewayModelWriteOut(BaseModel):
    """加线路、改线路、调优先级之后，这个模型更新后的那一项。"""

    model: GatewayModelItemOut


class GatewayDeletedOut(BaseModel):
    deleted: bool


class GatewayBlockedOut(BaseModel):
    blocked: bool
