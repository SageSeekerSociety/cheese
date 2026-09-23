"""后台「模型管理页」背后的服务 —— 页面上每个数字从哪来，都在这里定死。

这个模块把**两本账**拼成一张页面：网关那本（它路由了什么、每个模型花了多少、每个
项目密钥的预算）和平台那本（项目叫什么、还剩多少算力）。两本账的权威范围不同，谁
也不覆盖谁：

- **模型与按模型用量一律取网关**（`/model/info` 加 `daily/activity` 的
  `breakdown.model_groups`）。平台的 `resource_usage` 在排干网关流量时把 `model`
  写成部署默认模型（契约 §0），按它做模型归因是错的；本模块不拿它当模型用量。
- **项目额度取平台自己的算力账**（`UsageService.project_credits`）——那才是
  「这个项目还能花多少」的定义处。

网关侧答案缓存 15 秒（`_TTL_SECONDS`）：`/model/info` 与一周的用量响应都是百 KB
级，页面一进来就有一次列表加一次用量，给它们一个短 TTL 就够了，更短没有收益、更长
页面就开始骗人。缓存的是**网关的答案**（模型表、密钥表、用量窗口），不是拼好的
dict —— 平台那半每次现查，所以改了算力额度不必等缓存过期。

写操作的两条不变式由**这里**强制，不指望路由或界面：

- `config` 来源的模型一律拒绝写。网关自己也会拒（`Can't edit model. Model in
  config`），但那句话是说给我们听的，不该原样丢给用户；先把「要改就改
  `deploy/gateway/config.yaml` 并发布网关」说清楚。
- 上架（`cheese_selectable`）必须同时有输入与输出两个单价。理由不是洁癖：没有单
  价的模型网关按 0 计费，项目 `max_budget` 这道刹车会**静默失效**，唯一还能拦住
  它的就剩发票了 —— 与 `deploy/gateway/check_config.py` 是同一句话。

写操作还会 `reset_cache()` 并刷新 `gateway_catalog`：选择器用的是那份目录，写完不
刷，用户要等最长一个刷新周期才看得到新模型（或还看得到一个已停用的）。
"""

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as dtime
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.errors import BadRequestError, NotFoundError
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_admin import (
    AdminKey,
    AdminModel,
    GatewayAdmin,
    GatewayAdminError,
    GatewayUnreachable,
    ModelUsage,
    UsageWindow,
)
from app.domain.agent.models import GatewayAdminAudit
from app.domain.agent.schemas import ModelCreate, ModelUpdate
from app.domain.project.services import ProjectService
from app.domain.usage.services import UsageService

logger = logging.getLogger(__name__)

# 网关侧答案的存活时间。15 秒是「同一个人连点两下」和「页面自己在轮询」之间的那
# 个位置：短到不会让人看到过期的上线状态，长到一次页面加载只问网关一遍。
_TTL_SECONDS = 15.0

# 写操作的返回体要带一份 3.1 形状的模型项，而写接口的请求里没有 `days`。取列表页
# 的默认窗口，好让「刚写完看到的这一项」和「列表里那一项」是同一个口径。
_DEFAULT_DAYS = 7

# 查「这个模型在平台自己账上用了多少」时，`UsageService.by_model` 是一次
# top-N 聚合（模型是低基数维度）。给一个比任何真实部署的模型数都大的上界，就能把
# 目标那一行稳定地捞出来；真正的目的是**复用既有 SQL**，不在这里另写一份按模型的
# 聚合（契约 §6 明确不要拿 `resource_usage` 做模型用量，这里只作为带脚注的对照）。
_PLATFORM_MODEL_SCAN = 200

# 契约 §3.3：模型名 1..64 位，只允许这几类字符。
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# 单价与能力在网关管理 API 与页面契约里用同一套语义键；流转成 litellm_params 的
# `*_cost_per_token` 只发生在 `_prices_for_gateway` 一处。
_PRICE_KEYS = ("input", "output", "cache_read", "cache_creation")
# 审计去敏要认的字段名（大小写、连字符/下划线都归一）——只剔凭据，`api_base` 不动。
# 后三个是订阅导入的 OAuth 三件套：不扩这份名单，订阅的审计素材会把明文 token 落库。
_KEY_FIELD_NAMES = {"api_key", "apikey", "access_token", "refresh_token", "id_token"}

_cache: dict[str, tuple[float, Any]] = {}

# 缓存的代际。`reset_cache()` 让它 +1，`_cached` 写回前比对自己发起读时的代际。要它
# 是因为一次读要走 `await loader()`，期间可能有写操作把缓存失效（`reset_cache`）：没有
# 这道闸，那次读会在写之后把**写前**的旧答案写回缓存，盖掉刚做的失效，于是最长 TTL
# 内页面显示写前状态，而审计已经记了 ok。代际是唯一能区分「这次读属于哪个时代」的东西。
_cache_generation = 0


def reset_cache() -> None:
    """清空网关答案的缓存。写操作之后调它，测试也靠它把上一个用例的答案丢掉。"""
    global _cache_generation
    _cache_generation += 1
    _cache.clear()


async def _cached(key: str, loader: Callable[[], Awaitable[Any]]) -> Any:
    """读网关答案的缓存，过期就重问。loader 抛异常时什么都不缓存 —— 一次失败不
    应该被记成 15 秒的「答案」。

    写回前比对代际：若这次读期间发生过 `reset_cache()`，说明手上这份是失效前的旧
    答案，丢弃写回；仍照常返回给本次调用者 —— 我们手里没有比它更新的答案。
    """
    hit = _cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < _TTL_SECONDS:
        return hit[1]
    generation = _cache_generation
    value = await loader()
    if generation == _cache_generation:
        _cache[key] = (time.monotonic(), value)
    return value


def _utcnow() -> datetime:
    return datetime.now(UTC)


class GatewayModelsService:
    """管理页的服务层。``admin`` 不可达时按契约抛 `GatewayUnreachable`（路由转
    503），``admin`` 为 None（没配管理凭据）时列表页降级、写操作与项目页拒绝。

    ``gateway`` 是给 `gateway_catalog.refresh` 用的 `LlmGateway` 实例，由更外层
    注入（和 `admin` 同源，都可能为 None）。没注入时退回按配置现造一个 —— 刷新只
    在写操作之后发生，频率低，多造一个客户端不值得为它加一层依赖。
    """

    def __init__(
        self,
        db: AsyncSession,
        admin: GatewayAdmin | None,
        now: Callable[[], datetime] = _utcnow,
        *,
        gateway: LlmGateway | None = None,
    ) -> None:
        self._db = db
        self._admin = admin
        self._now = now
        self._gateway = gateway

    # ------------------------------------------------------------------
    # 时间窗口
    # ------------------------------------------------------------------
    def _today(self) -> date:
        now = self._now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        return now.astimezone(UTC).date()

    def _window(self, days: int) -> tuple[date, date, datetime, datetime]:
        """``(start, end, since, until)``。

        `start..end`（**含**当天）是传给网关的闭区间日期 —— 网关的 `end_date` 含当天。
        `days` 读作「回看几天」，再连上今天：`days=7` 就是 `today-7 .. today`，与契约
        §3.1 冻结的示例（`2026-09-16 .. 2026-09-23`、当天 `2026-09-23`）逐字一致。
        `since..until`（半开）给平台自己的仓储用，覆盖同样的这些天。
        """
        today = self._today()
        start = today - timedelta(days=days)
        since = datetime.combine(start, dtime.min, tzinfo=UTC)
        until = datetime.combine(today, dtime.min, tzinfo=UTC) + timedelta(days=1)
        return start, today, since, until

    def _window_dict(self, days: int, start: date, end: date) -> dict:
        return {
            "days": days,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }

    # ------------------------------------------------------------------
    # 网关答案（带缓存）
    # ------------------------------------------------------------------
    async def _gateway_up(self) -> None:
        """网关管理 API 可达吗？不可达就直接抛 —— 下面每个读法都只有网关能答。"""
        if self._admin is None:
            raise GatewayUnreachable("未配置网关管理凭据")
        if await self._readiness() is None:
            raise GatewayUnreachable("网关不可达")

    async def _readiness(self) -> str | None:
        assert self._admin is not None
        return await _cached("readiness", self._admin.readiness)

    async def _models_raw(self) -> list[AdminModel]:
        assert self._admin is not None
        return await _cached("models", self._admin.models)

    async def _keys_raw(self) -> list[AdminKey]:
        assert self._admin is not None
        return await _cached("keys", self._admin.keys)

    async def _usage_window(self, start: date, end: date) -> UsageWindow:
        admin = self._admin
        assert admin is not None
        key = f"usage:{start.isoformat()}:{end.isoformat()}"
        return await _cached(
            key, lambda: admin.usage(start.isoformat(), end.isoformat())
        )

    async def _require_models(self) -> list[AdminModel]:
        await self._gateway_up()
        return await self._models_raw()

    async def _current_model(self, name: str) -> AdminModel:
        models = await self._require_models()
        found = next((m for m in models if m.name == name), None)
        if found is None:
            raise NotFoundError(f"模型 {name} 不存在")
        return found

    async def _snapshot_model(self, name: str) -> dict | None:
        """写前那条模型的当前状态，给审计的 `before` 回答「原来是什么」。

        读不到（网关不可达、模型不存在）就返回 None：`before` 缺一列不该把写操作本
        身挡在门外 —— 这次读失败随后会在 `_update` / `_set_blocked` 里再发生一次，
        照常落一条 failed，该记的原因不会因此丢掉。
        """
        try:
            return _model_snapshot(await self._current_model(name))
        except (GatewayAdminError, NotFoundError):
            return None

    async def _snapshot_key(self, project_id: uuid.UUID) -> dict | None:
        """写前那把项目密钥的预算状态，给审计的 `before`。读不到就给 None。"""
        if self._admin is None:
            return None
        try:
            keys = await self._keys_raw()
        except GatewayAdminError:
            return None
        by_alias = {k.alias: k for k in keys}
        by_user = {k.user_id: k for k in keys if k.user_id}
        key = by_alias.get(f"project-{project_id}") or by_user.get(
            f"project:{project_id}"
        )
        return None if key is None else _key_snapshot(key)

    async def _after_write(self) -> None:
        """写成功后让缓存与目录都跟上：先失效，再让选择器那份目录重读网关。"""
        reset_cache()
        await gateway_catalog.refresh(self._catalog_gateway())

    def _catalog_gateway(self) -> LlmGateway | None:
        if self._gateway is not None:
            return self._gateway
        if not (settings.llm_gateway_admin_base and settings.llm_gateway_admin_key):
            return None
        return LlmGateway(
            settings.llm_gateway_admin_base, settings.llm_gateway_admin_key
        )

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def listing(self, *, days: int) -> dict:
        start, end, _, _ = self._window(days)
        window = self._window_dict(days, start, end)
        fetched_at = self._now().isoformat()

        if self._admin is None:
            # 没配管理凭据是**受支持的部署**（见 `deps.get_llm_gateway`）：页面照开，
            # 只是模型表空着、并明说原因。把它当 503 会让这类部署整个后台上不去。
            return {
                "gateway": {
                    "reachable": False,
                    "readiness": None,
                    "admin_configured": False,
                    "detail": "未配置网关管理凭据（llm_gateway_admin_base / key）",
                    "fetched_at": fetched_at,
                },
                "window": window,
                "totals": _usage_to_dict(None),
                "models": [],
            }

        # 配了凭据但不可达 → 交给路由转 503。列表页的头等事就是这个列表，答不出来
        # 就没有「部分正确」可给：一张空表会被读成「这里一个模型都没有」。
        models = await self._require_models()
        usage = await self._usage_window(start, end)
        overlay = await self._subscription_overlay()
        return {
            "gateway": {
                "reachable": True,
                "readiness": await self._readiness(),
                "admin_configured": True,
                "detail": None,
                "fetched_at": fetched_at,
            },
            "window": window,
            "totals": _usage_to_dict(usage.totals),
            "models": [self._item(m, usage, overlay.get(m.name)) for m in models],
        }

    async def detail(self, *, name: str, days: int) -> dict:
        start, end, since, until = self._window(days)
        model = await self._current_model(name)
        usage = await self._usage_window(start, end)
        overlay = await self._subscription_overlay()
        return {
            "model": self._item(model, usage, overlay.get(name)),
            "series": self._series(name, usage),
            "platform_usage": await self._platform_usage(name, since, until),
        }

    async def _subscription_overlay(self) -> dict[str, dict]:
        """订阅 overlay：linked_model_name → 订阅状态。走 SubscriptionService 的门
        （service → service，不碰对方仓储）。拼在 15s 缓存**之外**：它是平台库这
        一侧的答案，与项目额度同一个 freshness 纪律 —— 导入完立刻看得见徽章。

        读取失败（比如库还没迁移出这张表）降级成空 overlay，不让模型列表跟着
        殉葬 —— 列表的头等事是模型本身。
        """
        try:
            from app.domain.subscription.services import SubscriptionService

            service = SubscriptionService(self._db, None, None)
            return await service.status_by_linked_model()
        except Exception:  # noqa: BLE001 — overlay 是增强，不是列表的命门
            logger.warning("subscription overlay read failed", exc_info=True)
            return {}

    def _series(self, name: str, usage: UsageWindow) -> list[dict]:
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
        self, name: str, since: datetime, until: datetime
    ) -> dict:
        """平台自己账上这个模型的用量 —— 只作对照，**不是**模型用的那个数。

        网关流量的模型归因在 `resource_usage` 里是坏的（写的是部署默认模型，契约
        §0），所以这个数常常是 0 而网关那边有真用量。页面上它带着 `note` 出现，读
        的人该看网关那一列 —— 这一句就是那个提醒，不是装饰。
        """
        rows = await UsageService(self._db).by_model(
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

    async def projects(self, *, days: int) -> dict:
        start, end, _, _ = self._window(days)
        await self._gateway_up()
        keys = await self._keys_raw()
        usage = await self._usage_window(start, end)
        items = await self._project_items(keys, usage)
        return {
            "window": self._window_dict(days, start, end),
            "projects": items,
            "totals": {
                "projects": len(items),
                "with_key": sum(1 for i in items if i["has_key"]),
                # 「超预算」的判据是花掉的钱已经够到刹车值 —— 不是「快到了」。
                "over_budget": sum(
                    1
                    for i in items
                    if i["max_budget_usd"] is not None
                    and i["gateway_spend_usd"] >= i["max_budget_usd"]
                ),
                "unlimited": sum(1 for i in items if i["credits"]["unlimited"]),
            },
        }

    async def _project_items(
        self, keys: list[AdminKey], usage: UsageWindow
    ) -> list[dict]:
        by_alias = {k.alias: k for k in keys}
        by_user = {k.user_id: k for k in keys if k.user_id}
        projects, _total = await ProjectService(self._db).list_all()
        # 额度汇总一次批量取（teams 归属一条 JOIN、grants 一条 IN），不是逐项目
        # 各查一遍 —— 项目多的时候，2N+1 次串行往返和 3 次之间的差就是这段
        # 页面加载的肉眼差。口径与逐项目的 `project_credits` 逐字段相等
        # （`tests/unit/test_credits_batch.py` 钉着这件事）。
        summaries = await UsageService(self._db).project_credits_batch(projects)
        return [
            self._project_item(project, by_alias, by_user, usage, summaries[project.id])
            for project in projects
        ]

    def _project_item(
        self, project, by_alias, by_user, usage: UsageWindow, summary: dict
    ) -> dict:
        key = by_alias.get(f"project-{project.id}") or by_user.get(
            f"project:{project.id}"
        )
        # 刹车值的「应有」值是算力换算来的；unlimited（没有发放记录）时没有这个数，
        # 此时 key 上任何 max_budget 都是一次显式的覆盖。
        derived = None
        if not summary["unlimited"] and settings.llm_gateway_credit_usd is not None:
            derived = summary["credits_total"] * settings.llm_gateway_credit_usd
        max_budget = key.max_budget if key else None
        override = (
            max_budget if max_budget is not None and max_budget != derived else None
        )
        u: ModelUsage | None = usage.by_key.get(key.key_hash) if key else None
        return {
            "project_id": str(project.id),
            "name": project.name,
            "key_alias": key.alias if key else f"project-{project.id}",
            "has_key": key is not None,
            "gateway_spend_usd": key.spend if key else 0.0,
            "max_budget_usd": max_budget,
            "budget_derived_usd": derived,
            "budget_override_usd": override,
            "credits": {
                "total": summary["credits_total"],
                "used": summary["credits_used"],
                "remaining": summary["credits_remaining"],
                "unlimited": summary["unlimited"],
            },
            "usage": _usage_to_dict(u),
        }

    async def audit(self, *, limit: int) -> dict:
        stmt = (
            select(GatewayAdminAudit)
            .order_by(GatewayAdminAudit.created_at.desc())
            .limit(limit)
        )
        rows = (await self._db.scalars(stmt)).all()
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

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    async def add(self, *, handle: str, payload: ModelCreate) -> dict:
        data = payload.model_dump()
        name = str(data.get("name") or "")
        try:
            item = await self._add(name, data)
        except _WRITE_ERRORS as exc:
            await self._record(
                handle=handle,
                action="model.add",
                target=name,
                result="failed",
                detail=str(exc),
                after=data,
            )
            raise
        await self._record(
            handle=handle, action="model.add", target=name, result="ok", after=data
        )
        return {"model": item}

    async def _add(self, name: str, data: dict) -> dict:
        _validate_name(name)
        upstream_model = str(data.get("upstream_model") or "")
        if not upstream_model:
            # 上游标识是这条模型唯一「打到哪儿去」的答案，缺了它网关只会建出一条
            # 谁调谁失败的模型 —— 那不是模型，是一条陷阱。
            raise BadRequestError("上游模型标识不能为空")
        api_base = data.get("api_base")
        _validate_api_base(api_base)
        selectable = bool(data.get("selectable"))
        prices = data.get("prices") or {}
        _require_price_when_selectable(selectable, prices)

        await self._gateway_up()
        model_id = await self._admin.add_model(  # type: ignore[union-attr]
            name=name,
            upstream_model=upstream_model,
            api_base=api_base,
            api_key=data.get("api_key"),
            prices=_prices_for_gateway(prices),
            label=data.get("label"),
            selectable=selectable,
            capabilities=data.get("capabilities") or {},
            extra_headers=data.get("extra_headers"),
        )
        await self._after_write()
        return await self._read_back(name, data, model_id)

    async def update(self, *, handle: str, name: str, payload: ModelUpdate) -> dict:
        data = payload.model_dump(exclude_unset=True)
        before = await self._snapshot_model(name)
        try:
            item = await self._update(name, data)
        except _WRITE_ERRORS as exc:
            await self._record(
                handle=handle,
                action="model.update",
                target=name,
                result="failed",
                detail=str(exc),
                before=before,
                after=data,
            )
            raise
        await self._record(
            handle=handle,
            action="model.update",
            target=name,
            result="ok",
            before=before,
            after=data,
        )
        return {"model": item}

    async def _update(self, name: str, data: dict) -> dict:
        current = await self._current_model(name)
        _refuse_config_write(current)
        api_base = data.get("api_base")
        if api_base is not None:
            _validate_api_base(api_base)
        # 不变式永远看**合并之后**的样子：把已上架模型的单价删成一半，和一开始就
        # 没价，对刹车是同一种损坏。
        selectable = data.get("selectable", current.selectable)
        prices = data.get("prices")
        effective_prices = prices if prices is not None else current.prices
        _require_price_when_selectable(bool(selectable), effective_prices)

        kwargs: dict[str, Any] = {"model_id": current.model_id}
        for field in ("upstream_model", "api_base", "label", "selectable"):
            if field in data:
                kwargs[field] = data[field]
        # 编辑界面不回显上游凭据，所以「没填 key」是常态、不能当成「清空 key」。
        # 只有显式给了一个非空 key、且没声明 unchanged，才真的动凭据。
        api_key = data.get("api_key")
        if api_key and not data.get("api_key_unchanged"):
            kwargs["api_key"] = api_key
        if prices is not None:
            kwargs["prices"] = _prices_for_gateway(prices)
        if data.get("capabilities") is not None:
            kwargs["capabilities"] = data["capabilities"]
        # PATCH 合并语义在客户端兑现：缺省 = 不动既有头，订阅模型经表单改标签/
        # 价格时不会把订阅导入写进去的三件套弄丢。
        if data.get("extra_headers"):
            kwargs["extra_headers"] = data["extra_headers"]

        await self._admin.update_model(**kwargs)  # type: ignore[union-attr]
        await self._after_write()
        return await self._read_back(name, data, current.model_id)

    async def delete(self, *, handle: str, name: str) -> dict:
        try:
            await self._delete(name)
        except _WRITE_ERRORS as exc:
            await self._record(
                handle=handle,
                action="model.delete",
                target=name,
                result="failed",
                detail=str(exc),
            )
            raise
        await self._record(
            handle=handle, action="model.delete", target=name, result="ok"
        )
        return {"deleted": True}

    async def _delete(self, name: str) -> None:
        current = await self._current_model(name)
        _refuse_config_write(current)
        await self._admin.delete_model(current.model_id)  # type: ignore[union-attr]
        await self._after_write()

    async def set_blocked(self, *, handle: str, name: str, blocked: bool) -> dict:
        before = await self._snapshot_model(name)
        try:
            await self._set_blocked(name, blocked)
        except _WRITE_ERRORS as exc:
            await self._record(
                handle=handle,
                action="model.blocked",
                target=name,
                result="failed",
                detail=str(exc),
                before=before,
                after={"blocked": blocked},
            )
            raise
        await self._record(
            handle=handle,
            action="model.blocked",
            target=name,
            result="ok",
            before=before,
            after={"blocked": blocked},
        )
        return {"blocked": blocked}

    async def _set_blocked(self, name: str, blocked: bool) -> None:
        current = await self._current_model(name)
        # 网关不支持停用 config 模型 —— 页面上它对停用是只读的，这里也如实拒绝，
        # 而不是做一个只改平台数据的假象。
        _refuse_config_write(current, verb="停用/启用")
        await self._admin.set_blocked(current.model_id, blocked)  # type: ignore[union-attr]
        await self._after_write()

    async def set_budget(
        self, *, handle: str, project_id: uuid.UUID, max_budget_usd: float | None
    ) -> dict:
        before = await self._snapshot_key(project_id)
        try:
            item = await self._set_budget(project_id, max_budget_usd)
        except _WRITE_ERRORS as exc:
            await self._record(
                handle=handle,
                action="project.budget",
                target=str(project_id),
                result="failed",
                detail=str(exc),
                before=before,
                after={"max_budget_usd": max_budget_usd},
            )
            raise
        await self._record(
            handle=handle,
            action="project.budget",
            target=str(project_id),
            result="ok",
            before=before,
            after={"max_budget_usd": max_budget_usd},
        )
        return item

    async def _set_budget(
        self, project_id: uuid.UUID, max_budget_usd: float | None
    ) -> dict:
        project = await ProjectService(self._db).get(project_id)
        if project is None:
            raise NotFoundError(f"项目 {project_id} 不存在")
        await self._gateway_up()
        keys = await self._keys_raw()
        by_alias = {k.alias: k for k in keys}
        by_user = {k.user_id: k for k in keys if k.user_id}
        key = by_alias.get(f"project-{project.id}") or by_user.get(
            f"project:{project.id}"
        )
        if key is None:
            raise BadRequestError(f"项目「{project.name}」还没有网关密钥，无法设置预算")
        await self._admin.set_key_budget(  # type: ignore[union-attr]
            key.key_hash, max_budget_usd
        )
        await self._after_write()
        start, end, _, _ = self._window(_DEFAULT_DAYS)
        usage = await self._usage_window(start, end)
        keys = await self._keys_raw()
        by_alias = {k.alias: k for k in keys}
        by_user = {k.user_id: k for k in keys if k.user_id}
        summary = await UsageService(self._db).project_credits(project.id)
        return self._project_item(project, by_alias, by_user, usage, summary)

    # ------------------------------------------------------------------
    # 读回 + 审计
    # ------------------------------------------------------------------
    async def _read_back(self, name: str, data: dict, model_id: str) -> dict:
        """写完后按列表页同一口径读回这一项。

        读回失败、或网关刚写完还没把这一条反映到 `/model/info` 时，**不把已经成功
        的写报成失败**：退回用提交的内容拼一个最小项，页面下一拍刷新会拿到真值。
        """
        start, end, _, _ = self._window(_DEFAULT_DAYS)
        try:
            models = await self._models_raw()
            usage = await self._usage_window(start, end)
        except GatewayAdminError:
            return _item_from_payload(name, data, model_id)
        found = next((m for m in models if m.name == name), None)
        if found is not None:
            overlay = await self._subscription_overlay()
            return self._item(found, usage, overlay.get(name))
        return _item_from_payload(name, data, model_id, usage.by_model.get(name))

    async def _record(
        self,
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
        self._db.add(
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
        await self._db.flush()
        # 立刻提交，独立于这次请求的成败：路由的 `get_db` 在异常时会回滚，而
        # 「失败也落一行」正是审计存在的理由 —— 让失败记录跟着回滚就等于没记。
        await self._db.commit()

    # ------------------------------------------------------------------
    # 拼装
    # ------------------------------------------------------------------
    def _item(
        self,
        model: AdminModel,
        usage: UsageWindow,
        subscription: dict | None = None,
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
            "offered": offered,
            "blocked_reason": None if offered else _offered_reason(model),
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
            # 第三种来源徽章「订阅」的数据；没有订阅挂在这条模型上时是 None。
            "subscription": subscription,
        }
        if model.origin == "config":
            # 只对 config 模型给这段可复制文本：它唯一的改法是编辑 config.yaml，
            # 页面上直接给出该粘贴的那几行，省得人照着重打一遍再打错。
            item["config_yaml"] = _config_yaml(model)
        return item


# ----------------------------------------------------------------------
# 模块级小工具（无状态，测试可直接调）
# ----------------------------------------------------------------------
# 写操作里「服务自己判死」或「网关判死」的几种错。都落审计，然后原样抛给路由。
_WRITE_ERRORS = (GatewayAdminError, BadRequestError, NotFoundError)

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


def _offered_reason(model: AdminModel) -> str:
    """`offered=false` 时给一句人话，把**所有**挡住它的原因都列上 —— 只报第一个
    会让人改完发现还是上不了架。"""
    reasons: list[str] = []
    if model.blocked:
        reasons.append("已在网关停用，选择器不会提供它")
    if not model.selectable:
        reasons.append("未上架（网关里没有标 cheese_selectable）")
    if not model.priced:
        reasons.append(
            "未定价：缺输入或输出单价，上架会让项目的 max_budget 这道刹车静默失效"
        )
    return "；".join(reasons)


def _host_of(api_base: str | None) -> str | None:
    if not api_base:
        return None
    try:
        return urlsplit(api_base).netloc or None
    except ValueError:
        return None


def _price_is_positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _require_price_when_selectable(selectable: bool, prices: dict | None) -> None:
    if not selectable:
        return
    prices = prices or {}
    if not (
        _price_is_positive(prices.get("input"))
        and _price_is_positive(prices.get("output"))
    ):
        raise BadRequestError(
            "上架必须同时有输入与输出单价：无价模型会让项目的 max_budget 这道"
            "刹车静默失效"
        )


def _prices_for_gateway(prices: dict | None) -> dict:
    """页面契约的单价（null 表示「没有这一项」）→ 只留真正给了值的键交给客户端；
    不把 null 当 0 传下去 —— 那会把「没定价」写成「免费」。"""
    prices = prices or {}
    return {
        key: float(prices[key]) for key in _PRICE_KEYS if prices.get(key) is not None
    }


def _validate_name(name: str) -> None:
    if not _NAME_RE.match(name or ""):
        raise BadRequestError("模型名必须是 1..64 位的字母、数字、点、下划线或连字符")


def _validate_api_base(api_base: str | None) -> None:
    if api_base is None:
        return
    if not api_base.startswith("https://"):
        raise BadRequestError("上游 API 地址必须以 https:// 开头")


def _refuse_config_write(model: AdminModel, *, verb: str = "修改") -> None:
    if model.origin != "config":
        return
    raise BadRequestError(
        f"模型 {model.name} 由 deploy/gateway/config.yaml 声明，网关不接受在运行时"
        f"{verb}它；要改请编辑 deploy/gateway/config.yaml 并发布网关。"
    )


def _is_key_field(field: object) -> bool:
    return str(field).strip().lower().replace("-", "_") in _KEY_FIELD_NAMES


def _redact(value: Any) -> Any:
    """递归剔掉任何 `api_key` 字段 —— 审计素材里绝不留上游凭据（契约 §2.2）。"""
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items() if not _is_key_field(k)}
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
    }
    for key, field in fields.items():
        if key in model.prices:
            lines.append(f"    {field}: {model.prices[key]}")
    lines.append("  model_info:")
    lines.append(f"    cheese_selectable: {'true' if model.selectable else 'false'}")
    lines.append(f"    cheese_label: {model.label}")
    for capability, enabled in model.capabilities.items():
        if enabled:
            lines.append(f"    supports_{capability}: true")
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
        "upstream_model": model.upstream_model,
        "api_base": model.api_base,
        "provider": model.provider,
        "prices": dict(model.prices),
        "capabilities": dict(model.capabilities),
        # 非凭据（订阅上游的账号 id 与客户端标识），可进审计；api_key 一类的
        # 真凭据 `AdminModel` 本就从网关剥掉了。
        "extra_headers": dict(model.extra_headers),
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
        "offered": selectable and priced,
        "blocked_reason": None if (selectable and priced) else "等待网关读回",
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
        "subscription": None,
    }
