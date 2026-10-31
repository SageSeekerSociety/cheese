"""会抛的网关管理客户端:把管理动作的失败原样交给看得见它的人。

``gateway.py::LlmGateway`` 是给回合路径用的,失败语义是「吞掉、返回 None」——一条
turn 不能因为网关抽风就失败,所以那里把错误吃掉了。管理页正相反:是人点了「新增
模型」,失败了就得在页面上看到原因,一个沉默的 None 只会变成页面上莫名其妙的空白。
两种语义都合法,但塞进同一个对象会互相污染,所以「失败要抛」的那一半单独放这里,
``LlmGateway`` 的既有方法一字不改。

这里只做 HTTP 与解析:不做缓存(管理页看得本就少,且它要的是当下这一眼),不写审计
(那要落库、要记操作人,是服务层的事)。``api_key`` 是上游模型的凭据,只进请求体,
绝不回显、绝不进日志——把它写进响应或日志,等于把这条模型连同它的额度送人。

这一层只管取和折:要的形状与线路键住在 ``gateway_admin_types``,折的活住在
``gateway_admin_parsers``。本模块把两边导入进来用,也按原路径重导出;方向见下那条注释。
"""

import logging
from collections.abc import Mapping

import httpx

from app.core.sentences import say

# 这个模块从两个下层文件取名字：`gateway_admin_types`（线路键与页面形状，谁都不依赖）
# 和 `gateway_admin_parsers`（网关 JSON -> 页面形状的纯函数，只依赖 types）。方向是一条
# 直线：types <- parsers <- 本文件，没有回头路，
# `scripts/check_boundaries.py` 的 C3 才过得了。
#
# **本模块按原路径重导出上面这些名字** —— 单测和别处按
# `app.domain.agent.gateway_admin._xxx`、`.ModelUsage` 取名字，或者把这些名字当字符串
# 目标打补丁，都照旧；拆三个文件是为了让每一个都在行数上限内，
# 不是要把引用方赶上一次改名。
#
# 拆的边界是「谁需要谁」，不是「哪段看起来像另一件事」：解析函数要造的形状与要读的线路键
# 若是留在这里，就轮到解析层反过来导入本模块，同层成环——所以那批名字下沉到了 types。
#
# `noqa: F401` 的那些本模块不再引用：留着是为了本模块的名字集合不缩水 —— 少一个，
# `from app.domain.agent.gateway_admin import <它>` 就从导入期起炸。
#
# 没有跟着回来的是搬走的函数顺手用的那几个 `quote`、`dataclass`、`price_is_set`、
# `split_upstream`、`declared_free`：它们不是本模块的词汇，跟着回来只会让打在它们
# 身上的补丁看着还在、其实谁也不读了。要打在它们的调用处，就打到 parsers 上。
from app.domain.agent.gateway_admin_parsers import (
    _accumulate,
    _admin_key,
    _admin_model,
    _admin_source,  # noqa: F401
    _capability_params,
    _combine,  # noqa: F401
    _dict,  # noqa: F401
    _entries,
    _error_message,
    _int,  # noqa: F401
    _metrics_of,
    _num,  # noqa: F401
    _price_params,
    _price_value,  # noqa: F401
    _quote,
    _read_capabilities,  # noqa: F401
    _read_prices,  # noqa: F401
    _short,
    _source_info,
    _try_order,  # noqa: F401
    _unpriced_note,  # noqa: F401
    _usage,
    _window_totals,
    _zero_usage,  # noqa: F401
)
from app.domain.agent.gateway_admin_types import (
    _CAPABILITY_FIELDS,  # noqa: F401
    _PRICE_FIELDS,  # noqa: F401
    _SOURCE_INFO_KEYS,  # noqa: F401
    _UPSTREAM_INFO_KEYS,  # noqa: F401
    EXTRA_HEADERS,
    FREE,  # noqa: F401
    NO_EXPLICIT_CACHE,  # noqa: F401
    SESSION_HEADER,  # noqa: F401
    SPEND_CAP,
    UPSTREAM_HEADERS,  # noqa: F401
    AdminKey,
    AdminModel,
    AdminSource,  # noqa: F401
    ModelUsage,
    UsageWindow,
    catalogue_info,  # noqa: F401
)

logger = logging.getLogger(__name__)

# 与回合路径同一个超时。管理动作是人点出来的,等 8 秒还没答案就该报错,而不是让人
# 对着转圈发呆;网关 8 秒里回不来,多半是它自己出了问题,把原因显示出来比继续等有用。
_TIMEOUT = 8.0

# 一次分页取多少 key。本平台一把 key 对一个项目,量级几十上百,size=100 基本一次取完;
# 循环只是兜底,不让「项目多到一页之外」变成静默漏项。
_KEY_PAGE_SIZE = 100


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
            raise GatewayUnreachable(
                say("gatewayUnreachableWith", error=_short(exc))
            ) from exc

    @staticmethod
    async def _json(response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError as exc:
            raise GatewayRefused(say("gatewayNotJson")) from exc

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
        except Exception:  # 探活失败就是「不健康」,不是异常
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
            raise GatewayRefused(
                say("gatewayMissingList", path="/model/info", field="data")
            )
        groups: dict[str, list[dict]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = row.get("model_name")
            if isinstance(name, str) and name:
                groups.setdefault(name, []).append(row)
        return [_admin_model(name, group) for name, group in groups.items()]

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
                raise GatewayRefused(
                    say("gatewayMissingList", path="/key/list", field="keys")
                )
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

    async def usage(
        self, start_date: str, end_date: str, *, upstream: str | None = None
    ) -> UsageWindow:
        """窗口内的用量。按模型归因取 ``breakdown.model_groups``——它的键正好是
        ``/model/info`` 的 ``model_name``;``breakdown.models`` 用的是另一个名字,用错
        整张表就对不上号。按项目取 ``breakdown.api_keys``(键是 key 的 sha256,与
        ``/key/list`` 的 ``token`` 一致)。``breakdown.models`` 另记一份进
        ``by_upstream``,只给消费上限用:上限按上游模型名计账。窗口是闭区间,
        ``end_date`` 当天含在内。

        给了 ``upstream`` 就只读这个上游模型名下的账:网关按 ``model`` 参数筛
        ``LiteLLM_DailyUserSpend.model``,正是 spend_cap.py 拦截时求和的那一列。
        """
        params = {"start_date": start_date, "end_date": end_date}
        if upstream is not None:
            params["model"] = upstream
        response = await self._request(
            "GET", "/user/daily/activity/aggregated", params=params
        )
        payload = await self._json(response)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise GatewayRefused(say("gatewayUsageMissingResults"))

        daily: list[dict] = []
        by_model: dict[str, ModelUsage] = {}
        by_key: dict[str, ModelUsage] = {}
        by_upstream: dict[str, ModelUsage] = {}
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
            for upstream_name, entry in _entries(breakdown, "models").items():
                _accumulate(by_upstream, upstream_name, _usage(_metrics_of(entry)))
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
            by_upstream=by_upstream,
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
        tier: str | None = None,
        overseas: bool | None = None,
        order: int | None = None,
        free: bool | None = None,
        upstream_headers: Mapping[str, str] | None = None,
        session_header: str | None = None,
    ) -> str:
        """新建一个运行时模型(连同它的第一条线路),返回网关给的 model_id。

        ``api_key`` 只进请求体,绝不回显、绝不落日志——它是上游凭据。上游单价「两向
        都要有」这条不变式由服务层守,这里只如实传网关。
        """
        info: dict[str, object] = dict(_capability_params(capabilities))
        info["cheese_selectable"] = bool(selectable)
        if label is not None:
            info["cheese_label"] = label
        if tier is not None:
            info["cheese_tier"] = tier
        if overseas is not None:
            info["cheese_overseas"] = bool(overseas)
        return await self._new(
            name,
            upstream_model=upstream_model,
            api_base=api_base,
            api_key=api_key,
            prices=prices,
            info=info,
            order=order,
            free=free,
            upstream_headers=upstream_headers,
            session_header=session_header,
        )

    async def add_source(
        self,
        *,
        name: str,
        catalogue: Mapping[str, object],
        upstream_model: str,
        order: int,
        api_base: str | None = None,
        api_key: str | None = None,
        prices: Mapping[str, float | None] | None = None,
        free: bool | None = None,
        upstream_headers: Mapping[str, str] | None = None,
        session_header: str | None = None,
    ) -> str:
        """给已有模型加一条线路,返回它的 model_id。

        ``catalogue`` 是同组已有那一行里属于模型的元数据(``catalogue_info``),整份
        抄进新行的 model_info —— 新行自己的一份,不与谁共用(部署 id 由网关新发)。
        """
        return await self._new(
            name,
            upstream_model=upstream_model,
            api_base=api_base,
            api_key=api_key,
            prices=prices,
            info=dict(catalogue),
            order=order,
            free=free,
            upstream_headers=upstream_headers,
            session_header=session_header,
        )

    async def _new(
        self,
        name: str,
        *,
        upstream_model: str,
        api_base: str | None,
        api_key: str | None,
        prices: Mapping[str, float | None] | None,
        info: dict[str, object],
        order: int | None,
        free: bool | None,
        upstream_headers: Mapping[str, str] | None,
        session_header: str | None,
    ) -> str:
        params: dict[str, object] = {"model": upstream_model}
        if api_base:
            params["api_base"] = api_base
        if api_key:
            params["api_key"] = api_key
        params.update(_price_params(prices))
        if order is not None:
            params["order"] = order
        if upstream_headers:
            params[EXTRA_HEADERS] = dict(upstream_headers)
        info.update(_source_info(free, session_header))
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
            raise GatewayRefused(say("gatewayNoModelId"))
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
        tier: str | None = None,
        overseas: bool | None = None,
        order: int | None = None,
        free: bool | None = None,
        upstream_headers: Mapping[str, str] | None = None,
        session_header: str | None = None,
        spend_cap_usd: float | None = None,
    ) -> None:
        """改一条运行时部署(一个模型的一条线路)。``None`` 一律表示「这次不动它」。

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

        ``spend_cap_usd=0`` clears the cap: the merge drops a null, and
        ``spend_cap.declared_cap`` reads a cap of 0 or less as none.
        """
        info: dict[str, object] = {}
        if label is not None:
            info["cheese_label"] = label
        if selectable is not None:
            info["cheese_selectable"] = bool(selectable)
        if tier is not None:
            info["cheese_tier"] = tier
        if overseas is not None:
            info["cheese_overseas"] = bool(overseas)
        info.update(_capability_params(capabilities))
        info.update(_source_info(free, session_header))
        if spend_cap_usd is not None:
            info[SPEND_CAP] = spend_cap_usd
        params: dict[str, object] = {}
        if order is not None:
            params["order"] = order
        if upstream_headers is not None:
            # 整份替换：网关按键合并 litellm_params，`extra_headers` 这一键被新值
            # 盖掉；`{}` 就是一个头都不发。
            params[EXTRA_HEADERS] = dict(upstream_headers)
        if upstream_model is not None:
            params["model"] = upstream_model
        if api_base is not None:
            params["api_base"] = api_base
        if api_key is not None:
            params["api_key"] = api_key
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

    async def set_key_models(self, alias: str, models: list[str]) -> bool:
        """Which models the virtual key under ``alias`` may call. False when no
        key holds the alias yet (it is minted on first use, already with the
        role's model).

        By ``key_alias``: the platform's own keys are found by the alias they
        were minted under (``service_keys.KeySpec.alias``), and LiteLLM 1.103.3's
        ``/key/update`` looks a key up by it when ``key`` is left out, answering
        404 when none holds it.
        """
        try:
            await self._request(
                "POST", "/key/update", json={"key_alias": alias, "models": models}
            )
        except GatewayRefused as exc:
            if exc.status == 404:
                return False
            raise
        return True
