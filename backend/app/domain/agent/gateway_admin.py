"""会抛的网关管理客户端:把管理动作的失败原样交给看得见它的人。

``gateway.py::LlmGateway`` 是给回合路径用的,失败语义是「吞掉、返回 None」——一条
turn 不能因为网关抽风就失败,所以那里把错误吃掉了。管理页正相反:是人点了「新增
模型」,失败了就得在页面上看到原因,一个沉默的 None 只会变成页面上莫名其妙的空白。
两种语义都合法,但塞进同一个对象会互相污染,所以「失败要抛」的那一半单独放这里,
``LlmGateway`` 的既有方法一字不改。

这里只做 HTTP 与解析:不做缓存(管理页看得本就少,且它要的是当下这一眼),不写审计
(那要落库、要记操作人,是服务层的事)。``api_key`` 是上游模型的凭据,只进请求体,
绝不回显、绝不进日志——把它写进响应或日志,等于把这条模型连同它的额度送人。
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.domain.agent.gateway import price_is_set

logger = logging.getLogger(__name__)

# 与回合路径同一个超时。管理动作是人点出来的,等 8 秒还没答案就该报错,而不是让人
# 对着转圈发呆;网关 8 秒里回不来,多半是它自己出了问题,把原因显示出来比继续等有用。
_TIMEOUT = 8.0

# 一次分页取多少 key。本平台一把 key 对一个项目,量级几十上百,size=100 基本一次取完;
# 循环只是兜底,不让「项目多到一页之外」变成静默漏项。
_KEY_PAGE_SIZE = 100

# 我方对外用短名,网关用 LiteLLM 的字段名。翻译只在这一处发生,别让字段名散到服务层
# 和前端去——那样改一个名字要同时改三处,漏一处就是一处静默对不上。
_PRICE_FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_creation": "cache_creation_input_token_cost",
}

# 能力位同理:网关用 supports_*/adaptive 一整套名字,页面只关心这三件。
_CAPABILITY_FIELDS = {
    "reasoning": "supports_reasoning",
    "vision": "supports_vision",
    "adaptive_thinking": "supports_adaptive_thinking",
}


class GatewayAdminError(Exception):
    """管理动作失败。

    ``status`` 是网关的 HTTP 码(不可达时 None),``message`` 是给人看的原因。
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GatewayUnreachable(GatewayAdminError):
    """连不上或超时——网关没答话,不代表它拒绝了这次动作。"""


class GatewayRefused(GatewayAdminError):
    """网关答了话,而且拒绝了(4xx/5xx),例如对 config.yaml 里的模型做写操作。"""


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
    upstream_model: str
    api_base: str | None
    provider: str
    prices: dict[str, float]  # input/output/cache_read/cache_creation,缺的键不出现
    capabilities: dict[str, bool]  # reasoning / vision / adaptive_thinking
    supports_notes: str | None  # 缺价的原因等人话,priced=False 时给出
    # 随每次调用透传给上游的额外头（ChatGPT codex 后端要 chatgpt-account-id
    # 等三件套）。不是凭据,但也不算页面要展示的东西——读回只为审计快照。
    extra_headers: dict[str, str]


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


class GatewayAdmin:
    """管理 API 的薄客户端。

    ``transport`` 是测试缝(httpx.MockTransport),None = 真网络。
    """

    def __init__(
        self,
        base: str,
        admin_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = base.rstrip("/")
        self._headers = {"Authorization": f"Bearer {admin_key}"}
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=_TIMEOUT, transport=self._transport)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        params: Mapping[str, str | int] | None = None,
    ) -> httpx.Response:
        """一次管理调用。失败在这里就抛,不往上带一个「可能是 None」的答案。

        连不上/超时与「网关拒绝」要分开:前者 ``GatewayUnreachable``(页面报 503,
        是网关没答话),后者 ``GatewayRefused``(页面报 502,带上网关给的原因)。混成
        一个,页面就没法告诉人「是网关挂了还是你的动作不被允许」。
        """
        try:
            async with self._client() as client:
                response = await client.request(
                    method,
                    f"{self._base}{path}",
                    headers=self._headers,
                    json=json,
                    params=params,
                )
                response.raise_for_status()
                return response
        except httpx.HTTPStatusError as exc:
            raise GatewayRefused(
                _error_message(exc.response), status=exc.response.status_code
            ) from exc
        except httpx.HTTPError as exc:
            raise GatewayUnreachable(f"网关不可达:{_short(exc)}") from exc

    @staticmethod
    async def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayRefused("网关返回了非 JSON 响应") from exc

    async def readiness(self) -> str | None:
        """页面顶上那颗「网关在不在」的灯。不可达给 None,不抛。

        这是一次探活,不是一次动作:灯灭了页面照样要打开,只是把模型列表标成不可用,
        而不是整页 500。
        """
        try:
            async with self._client() as client:
                response = await client.get(
                    f"{self._base}/health/readiness", headers=self._headers
                )
                response.raise_for_status()
                body = response.json()
        except Exception:  # noqa: BLE001 — 探活失败就是「不健康」,不是异常
            logger.warning("gateway readiness probe failed", exc_info=True)
            return None
        status = body.get("status") if isinstance(body, dict) else None
        return status if isinstance(status, str) and status else "healthy"

    async def models(self) -> list[AdminModel]:
        """``/model/info`` 的每一条,转成管理页要的形状。

        不可达/被拒时抛,不返回空表——空表会被页面读成「这个网关没有模型」,而真相是
        「这次没读到」,两句话在对人说的话里正相反。
        """
        response = await self._request("GET", "/model/info")
        payload = await self._json(response)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise GatewayRefused("网关 /model/info 的响应里没有 data 列表")
        out: list[AdminModel] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("model_name")
            if not isinstance(name, str) or not name or name in seen:
                continue
            seen.add(name)
            out.append(_admin_model(name, row))
        return out

    async def keys(self) -> list[AdminKey]:
        """全部虚拟 key,分页取完。项目额度这一列就落在这里的 ``max_budget`` 上。"""
        out: list[AdminKey] = []
        page = 1
        while True:
            response = await self._request(
                "GET",
                "/key/list",
                params={
                    "return_full_object": "true",
                    "page": page,
                    "size": _KEY_PAGE_SIZE,
                },
            )
            payload = await self._json(response)
            rows = payload.get("keys") if isinstance(payload, dict) else None
            if not isinstance(rows, list):
                raise GatewayRefused("网关 /key/list 的响应里没有 keys 列表")
            for row in rows:
                if isinstance(row, dict):
                    out.append(_admin_key(row))
            total_pages = (
                payload.get("total_pages") if isinstance(payload, dict) else None
            )
            if not rows or not isinstance(total_pages, int) or page >= total_pages:
                break
            page += 1
        return out

    async def usage(self, start_date: str, end_date: str) -> UsageWindow:
        """窗口内的用量。按模型归因取 ``breakdown.model_groups``——它的键正好是
        ``/model/info`` 的 ``model_name``;``breakdown.models`` 用的是另一个名字,用错
        整张表就对不上号。按项目取 ``breakdown.api_keys``(键是 key 的 sha256,与
        ``/key/list`` 的 ``token`` 一致)。窗口是闭区间,``end_date`` 当天含在内。
        """
        response = await self._request(
            "GET",
            "/user/daily/activity/aggregated",
            params={"start_date": start_date, "end_date": end_date},
        )
        payload = await self._json(response)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise GatewayRefused("网关用量接口的响应里没有 results 列表")

        daily: list[dict] = []
        by_model: dict[str, ModelUsage] = {}
        by_key: dict[str, ModelUsage] = {}
        for row in results:
            if not isinstance(row, dict):
                continue
            breakdown = row.get("breakdown")
            day_models: dict[str, ModelUsage] = {}
            for model_name, entry in _entries(breakdown, "model_groups").items():
                usage = _usage(_metrics_of(entry))
                day_models[model_name] = usage
                _accumulate(by_model, model_name, usage)
            for key_hash, entry in _entries(breakdown, "api_keys").items():
                _accumulate(by_key, key_hash, _usage(_metrics_of(entry)))
            daily.append(
                {
                    "date": str(row.get("date") or ""),
                    "totals": _usage(row.get("metrics")),
                    "by_model": day_models,
                }
            )
        return UsageWindow(
            start_date=start_date,
            end_date=end_date,
            totals=_window_totals(payload, daily),
            by_model=by_model,
            by_key=by_key,
            daily=daily,
        )

    async def add_model(
        self,
        *,
        name: str,
        upstream_model: str,
        api_base: str | None = None,
        api_key: str | None = None,
        prices: Mapping[str, float | None] | None = None,
        label: str | None = None,
        selectable: bool = False,
        capabilities: Mapping[str, bool] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> str:
        """新建一条运行时模型,返回网关给的 model_id。

        ``api_key`` 只进请求体,绝不回显、绝不落日志——它是上游凭据。上游单价「两向
        都要有」这条不变式由服务层守,这里只如实传网关。``extra_headers`` 是随每次
        调用透传给上游的额外头(订阅型上游的账号三件套),非空时才写进
        ``litellm_params``。
        """
        params: dict[str, object] = {"model": upstream_model}
        if api_base:
            params["api_base"] = api_base
        if api_key:
            params["api_key"] = api_key
        if extra_headers:
            params["extra_headers"] = dict(extra_headers)
        params.update(_price_params(prices))
        info: dict[str, object] = dict(_capability_params(capabilities))
        info["cheese_selectable"] = bool(selectable)
        if label is not None:
            info["cheese_label"] = label
        response = await self._request(
            "POST",
            "/model/new",
            json={
                "model_name": name,
                "litellm_params": params,
                "model_info": info,
            },
        )
        payload = await self._json(response)
        model_id = payload.get("model_id") if isinstance(payload, dict) else None
        if not isinstance(model_id, str) or not model_id:
            raise GatewayRefused("网关没有返回新模型的 model_id")
        return model_id

    async def update_model(
        self,
        *,
        model_id: str,
        upstream_model: str | None = None,
        api_base: str | None = None,
        api_key: str | None = None,
        prices: Mapping[str, float | None] | None = None,
        label: str | None = None,
        selectable: bool | None = None,
        capabilities: Mapping[str, bool] | None = None,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        """改一条运行时模型。``None`` 一律表示「这次不动它」。

        走 ``PATCH /model/{model_id}/update``,不是 ``POST /model/update``。后者在
        LiteLLM 1.96.0 的 handler(``update_model``)里落库时只写 ``litellm_params``
        与 ``updated_by``,**根本不写 ``model_info``**——改标签、改上架、改能力会被
        静默丢掉;而且请求体不带 ``litellm_params`` 时它直接
        ``raise Exception("litellm_params not provided")`` → 400。管理页改的恰恰
        多是 model_info 那几项,所以那条路对本页是坏的。PATCH 那条
        (``patch_model`` → ``update_db_model``)把 ``litellm_params`` 与
        ``model_info`` 一起合并写入,model_id 走路径。两侧分支都是「传了才合并」,
        所以只带 ``model_info``、不带 ``litellm_params`` 不会被拒——不必先读回旧值
        再写回。

        编辑界面不回显上游凭据,所以 ``api_key=None`` 必须意味着保持原样,而不是把
        key 清空——把 key 清成空,这条模型下一次调用就直接失败了。这份「保持原样」
        由网关的合并语义兑现:字段不出现,库里旧值就不动。
        """
        info: dict[str, object] = {}
        if label is not None:
            info["cheese_label"] = label
        if selectable is not None:
            info["cheese_selectable"] = bool(selectable)
        info.update(_capability_params(capabilities))
        params: dict[str, object] = {}
        if upstream_model is not None:
            params["model"] = upstream_model
        if api_base is not None:
            params["api_base"] = api_base
        if api_key is not None:
            params["api_key"] = api_key
        if extra_headers:
            # PATCH 合并语义:缺省( None / 空)= 不动既有头;给了才整组替换。
            params["extra_headers"] = dict(extra_headers)
        if prices is not None:
            params.update(_price_params(prices))
        body: dict[str, object] = {}
        if info:
            body["model_info"] = info
        if params:
            body["litellm_params"] = params
        await self._request("PATCH", f"/model/{_quote(model_id)}/update", json=body)

    async def delete_model(self, model_id: str) -> None:
        """删掉一条运行时模型。config 里的模型网关会拒绝——服务层应先拦下。"""
        await self._request("POST", "/model/delete", json={"id": model_id})

    async def set_blocked(self, model_id: str, blocked: bool) -> None:
        """停用/启用一条运行时模型。停用后网关不再路由它,选择器里也不该再出现。"""
        path = "/model/block" if blocked else "/model/unblock"
        await self._request("POST", path, json={"model_id": model_id})

    async def set_key_budget(self, key_hash: str, max_budget_usd: float | None) -> None:
        """项目额度这道刹车,落在它那把虚拟 key 的 ``max_budget`` 上。

        ``/key/update`` 认的 ``key`` 字段既收 key hash 也收 key_alias(实测两者都行);
        这里用 hash——``/key/list`` 的 ``token`` 直接就是它,不必再拼别名,别名重名时
        也会打错对象。``max_budget_usd=None`` 是清空这道刹车(回到不限)。
        """
        await self._request(
            "POST",
            "/key/update",
            json={"key": key_hash, "max_budget": max_budget_usd},
        )


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
        return text[:300] if text else f"网关返回 HTTP {response.status_code}"
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
    return str(body)[:300] if body else f"网关返回 HTTP {response.status_code}"


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
        if short in capabilities:
            out[field] = bool(capabilities[short])
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


def _admin_model(name: str, row: dict) -> AdminModel:
    raw_info = row.get("model_info")
    info = raw_info if isinstance(raw_info, dict) else {}
    raw_params = row.get("litellm_params")
    params = raw_params if isinstance(raw_params, dict) else {}
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
    return AdminModel(
        name=name,
        model_id=model_id if isinstance(model_id, str) and model_id else name,
        label=label if isinstance(label, str) and label else name,
        origin="runtime" if info.get("db_model") is True else "config",
        blocked=info.get("blocked") is True,
        selectable=info.get("cheese_selectable") is True,
        priced=price_is_set(params, info),
        upstream_model=upstream if isinstance(upstream, str) else "",
        api_base=api_base if isinstance(api_base, str) and api_base else None,
        provider=provider,
        prices=_read_prices(params, info),
        capabilities=_read_capabilities(info),
        supports_notes=_unpriced_note(params, info),
        extra_headers=_read_extra_headers(params),
    )


def _read_extra_headers(params: object) -> dict[str, str]:
    """``litellm_params.extra_headers`` 原样读回（只要 str→str 的项）。不是凭据,
    但只服务审计快照——页面不展示它。"""
    if not isinstance(params, dict):
        return {}
    raw = params.get("extra_headers")
    if not isinstance(raw, dict):
        return {}
    return {str(k): v for k, v in raw.items() if isinstance(v, str)}


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
