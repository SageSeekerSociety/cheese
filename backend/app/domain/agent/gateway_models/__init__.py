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
  它的就剩发票了 —— 与 `deploy/gateway/check_config.py` 是同一句话。一个模型有几个
  线路，每条线路各算一遍：哪一个没价，落到它那里的流量就按零计。唯一合法的零是
  声明出来的：`cheese_free: true` 且输入、输出都写成 0（同 check_config）。

一个模型（`model_name`）可以有好几条线路（网关里的几条部署），按 `order` 从小到大试。
config.yaml 声明的线路只读，但照样列出来，人看得到完整的先后；运行时可以往任何模型
（包括 config 声明的）上加线路。模型那一层的元数据（上架、名字、档位、思考档、能力）
同组每一行一字不差：目录读 `/model/info` 列出的第一行，而哪一行排第一不由我们定。
所以加线路时从已有那一行抄，改模型时每一行都改。

写操作还会 `reset_cache()` 并刷新 `gateway_catalog`：选择器用的是那份目录，写完不
刷，用户要等最长一个刷新周期才看得到新模型（或还看得到一个已停用的）。

**这个包是服务的门面，服务按职责拆开。** 2026-10 之前是一个 1,119 行的
``gateway_models.py``；现在正文住在同一个目录的几个子模块里，``__init__.py`` 留下
的是树按**这个模块**去拿的东西：

- ``GatewayModelsService`` 保持它原来的每一个方法，每个方法在类体里只有一行转发，
  把 ``self`` 交给子模块的函数。调用方、类型注解、按名字打补丁的目标都照旧解析到
  这里 —— 门面**不加决定**，这正是两处不会各自长歪的原因。
- 常量、``reset_cache()``、``_cache`` 与 ``gateway_models_helpers`` 那批名字，照旧
  从这个模块取得到（见下面的重导出注释）。

每块正文去了哪：``cache``（网关答案的 15 秒缓存与它的代际），``answers``（可达性、
模型表、密钥表、用量窗口，以及写完之后让它们跟上），``models``（列表页、详情页、
模型本身的增删改停用、审计的读法），``projects``（项目页与预算那道刹车），
``sources``（线路的加改删停与重排），``guards``（停用、删除之前的那几道检查），``_shared``（时间窗口、审计落行、写完读回、页面
项拼装 —— 被两处以上用的那几样）。转发器本身不写 docstring，说明留在它要调的那个
模块函数上（``device_hub``、``feedback/services`` 都是这么放的）。

**为什么是包，不是六个扁平的兄弟。** C3（``sibling domains under app.domain are
acyclic``）把 ``app.domain`` 的孩子和它们各自的孩子都算作兄弟，而这些正文互相调用
—— 扁平放就是六对新的兄弟边。放进包里，内部只有 ``answers → cache`` 一条边，
``_shared`` 不被任何子模块导入，形状与 ``app/domain/agent/device_hub``、
``app/domain/feedback/services`` 一样。

与上面这套编排正交的纯函数层住在 `gateway_models_helpers`：数据转换、用量聚合、价格
校验、密钥脱敏、快照构造，全是无状态的。本模块把它们导入进来用，**也按原路径重导出**，
`app.domain.agent.gateway_models._xxx` 照旧取得到 —— 拆两个文件是为了让每一个都在行数
上限内，不是要把引用方赶上一次改名。
"""

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent import gateway_catalog  # noqa: F401
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_admin import (
    AdminKey,
    AdminModel,
    AdminSource,
    GatewayAdmin,
    GatewayAdminError,  # noqa: F401
    GatewayUnreachable,  # noqa: F401
    ModelUsage,  # noqa: F401
    UsageWindow,
)
from app.domain.agent.gateway_models import (
    _shared,
    answers,
    models,
    projects,
    roles,
    sources,
)
from app.domain.agent.gateway_models._shared import (
    _DEFAULT_DAYS,  # noqa: F401
    _MID_SYSTEM,  # noqa: F401
    _MID_SYSTEM_FIELD,  # noqa: F401
    _WRITE_ERRORS,  # noqa: F401
    _utcnow,
)
from app.domain.agent.gateway_models.cache import (
    _TTL_SECONDS,  # noqa: F401
    _cache,  # noqa: F401
    _cached,  # noqa: F401
    reset_cache,  # noqa: F401
)
from app.domain.agent.gateway_models.models import (
    _FIRST_ORDER,  # noqa: F401
    _PLATFORM_MODEL_SCAN,  # noqa: F401
)

# 纯函数层住在 `gateway_models_helpers`，这里按原路径重导出：服务自己要用其中大半，
# 外部与单测也可能按 `app.domain.agent.gateway_models._xxx` 取名字、或把它当字符串
# 目标打补丁（团队吃过拆模块别名、按字符串打的补丁在解析期炸掉的亏）。少这一层，
# 这类引用要到导入期或打补丁时才报错。
#
# 拆成包之后这个门面不再读它们（正文住在子模块里），`noqa: F401` 就是这条承诺的
# 代价：去掉一个，`from app.domain.agent.gateway_models import <它>` 从导入期起就
# 开始失败，而失败的地方离这里很远。同一条规矩在 `device_hub`、`gateway_admin` 也
# 写着。
from app.domain.agent.gateway_models_helpers import (
    # 这四个常量随纯函数层一起搬走，重导出是为了让本模块的名字集合一个不少：
    # `_NAME_RE` 认模型名、`_PRICE_KEYS` 认单价键、`_KEY_FIELD_NAMES` 认凭据字段名，
    # `_ZERO_USAGE` 是「没用过」的那份零。它们只被搬走的函数读，所以本模块不再引用。
    _KEY_FIELD_NAMES,  # noqa: F401
    _NAME_RE,  # noqa: F401
    _PRICE_KEYS,  # noqa: F401
    _ZERO_USAGE,  # noqa: F401
    _api_base_for,  # noqa: F401
    _bare_upstream,  # noqa: F401
    _config_yaml,  # noqa: F401
    _daily_model_row,  # noqa: F401
    _daily_tokens,  # noqa: F401
    _host_of,  # noqa: F401
    _is_key_field,  # noqa: F401
    _item_from_payload,  # noqa: F401
    _key_snapshot,  # noqa: F401
    _model_snapshot,  # noqa: F401
    _offered_reasons,  # noqa: F401
    _price_is_positive,  # noqa: F401
    _prices_for_gateway,  # noqa: F401
    _redact,  # noqa: F401
    _refuse_config_source,  # noqa: F401
    _refuse_config_write,  # noqa: F401
    _require_price_when_selectable,  # noqa: F401
    _scrub,  # noqa: F401
    _secrets_in,  # noqa: F401
    _source_item,  # noqa: F401
    _source_prices,  # noqa: F401
    _source_snapshot,  # noqa: F401
    _source_target,  # noqa: F401
    _translated_to_chat,  # noqa: F401
    _upstream_of,  # noqa: F401
    _usage_to_dict,  # noqa: F401
    _validate_api_base,  # noqa: F401
    _validate_name,  # noqa: F401
    _would_lead,  # noqa: F401
)
from app.domain.agent.models import GatewayAdminAudit  # noqa: F401
from app.domain.agent.schemas import (
    ModelCreate,
    ModelUpdate,
    SourceCreate,
    SourceUpdate,
    upstream_string,  # noqa: F401
)
from app.domain.project.services import ProjectService  # noqa: F401
from app.domain.usage.services import UsageService  # noqa: F401


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
        return _shared._today(self)

    def _window(self, days: int) -> tuple[date, date, datetime, datetime]:
        return _shared._window(self, days)

    def _window_dict(self, days: int, start: date, end: date) -> dict:
        return _shared._window_dict(days, start, end)

    # ------------------------------------------------------------------
    # 网关答案（带缓存）
    # ------------------------------------------------------------------
    async def _gateway_up(self) -> None:
        return await answers._gateway_up(self)

    async def _readiness(self) -> str | None:
        return await answers._readiness(self)

    async def _models_raw(self) -> list[AdminModel]:
        return await answers._models_raw(self)

    async def _keys_raw(self) -> list[AdminKey]:
        return await answers._keys_raw(self)

    async def _usage_window(self, start: date, end: date) -> UsageWindow:
        return await answers._usage_window(self, start, end)

    async def _ledger_window(self, start: date, end: date) -> UsageWindow:
        return await answers._ledger_window(self, start, end)

    async def _require_models(self) -> list[AdminModel]:
        return await answers._require_models(self)

    async def _current_model(self, name: str) -> AdminModel:
        return await answers._current_model(self, name)

    async def _snapshot_model(self, name: str) -> dict | None:
        return await answers._snapshot_model(self, name)

    async def _snapshot_key(self, project_id: uuid.UUID) -> dict | None:
        return await answers._snapshot_key(self, project_id)

    async def _after_write(self) -> None:
        return await answers._after_write(self)

    def _catalog_gateway(self) -> LlmGateway | None:
        return answers._catalog_gateway(self)

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def listing(self, *, days: int) -> dict:
        return await models.listing(self, days=days)

    async def detail(self, *, name: str, days: int) -> dict:
        return await models.detail(self, name=name, days=days)

    async def roles(self) -> dict:
        return await roles.listing(self)

    async def set_role(self, *, handle: str, role: str, model: str | None) -> dict:
        return await roles.set_role(self, handle=handle, role=role, model=model)

    def _series(self, name: str, usage: UsageWindow) -> list[dict]:
        return models._series(name, usage)

    async def _platform_usage(
        self, name: str, since: datetime, until: datetime
    ) -> dict:
        return await models._platform_usage(self, name, since, until)

    async def projects(self, *, days: int) -> dict:
        return await projects.projects(self, days=days)

    async def _project_items(
        self, keys: list[AdminKey], usage: UsageWindow
    ) -> list[dict]:
        return await projects._project_items(self, keys, usage)

    def _project_item(
        self, project, by_alias, by_user, usage: UsageWindow, summary: dict
    ) -> dict:
        return projects._project_item(project, by_alias, by_user, usage, summary)

    async def audit(self, *, limit: int) -> dict:
        return await models.audit(self, limit=limit)

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    async def add(self, *, handle: str, payload: ModelCreate) -> dict:
        return await models.add(self, handle=handle, payload=payload)

    async def _add(self, name: str, data: dict) -> dict:
        return await models._add(self, name, data)

    async def update(self, *, handle: str, name: str, payload: ModelUpdate) -> dict:
        return await models.update(self, handle=handle, name=name, payload=payload)

    async def _update(self, name: str, data: dict) -> dict:
        return await models._update(self, name, data)

    async def delete(self, *, handle: str, name: str) -> dict:
        return await models.delete(self, handle=handle, name=name)

    async def _delete(self, name: str) -> None:
        return await models._delete(self, name)

    async def set_blocked(self, *, handle: str, name: str, blocked: bool) -> dict:
        return await models.set_blocked(self, handle=handle, name=name, blocked=blocked)

    async def _set_blocked(self, name: str, blocked: bool) -> None:
        return await models._set_blocked(self, name, blocked)

    # ------------------------------------------------------------------
    # 线路：一个模型名下的一条部署
    # ------------------------------------------------------------------
    async def _audited(
        self,
        *,
        handle: str,
        action: str,
        target: str,
        work: Callable[[], Awaitable[dict]],
        before: dict | None = None,
        after: dict | None = None,
    ) -> dict:
        return await sources._audited(
            self,
            handle=handle,
            action=action,
            target=target,
            work=work,
            before=before,
            after=after,
        )

    async def _current_source(
        self, name: str, source_id: str
    ) -> tuple[AdminModel, AdminSource]:
        return await sources._current_source(self, name, source_id)

    async def _snapshot_source(self, name: str, source_id: str) -> dict | None:
        return await sources._snapshot_source(self, name, source_id)

    async def _fold_system_messages(self, model: AdminModel) -> None:
        return await sources._fold_system_messages(self, model)

    async def add_source(
        self, *, handle: str, name: str, payload: SourceCreate
    ) -> dict:
        return await sources.add_source(self, handle=handle, name=name, payload=payload)

    async def update_source(
        self, *, handle: str, name: str, source_id: str, payload: SourceUpdate
    ) -> dict:
        return await sources.update_source(
            self,
            handle=handle,
            name=name,
            source_id=source_id,
            payload=payload,
        )

    async def delete_source(self, *, handle: str, name: str, source_id: str) -> dict:
        return await sources.delete_source(
            self, handle=handle, name=name, source_id=source_id
        )

    async def set_source_blocked(
        self, *, handle: str, name: str, source_id: str, blocked: bool
    ) -> dict:
        return await sources.set_source_blocked(
            self,
            handle=handle,
            name=name,
            source_id=source_id,
            blocked=blocked,
        )

    async def reorder_sources(
        self, *, handle: str, name: str, orders: dict[str, int]
    ) -> dict:
        return await sources.reorder_sources(
            self, handle=handle, name=name, orders=orders
        )

    async def set_budget(
        self, *, handle: str, project_id: uuid.UUID, max_budget_usd: float | None
    ) -> dict:
        return await projects.set_budget(
            self,
            handle=handle,
            project_id=project_id,
            max_budget_usd=max_budget_usd,
        )

    async def _set_budget(
        self, project_id: uuid.UUID, max_budget_usd: float | None
    ) -> dict:
        return await projects._set_budget(self, project_id, max_budget_usd)

    # ------------------------------------------------------------------
    # 读回 + 审计
    # ------------------------------------------------------------------
    async def _read_back(self, name: str, data: dict, model_id: str) -> dict:
        return await _shared._read_back(self, name, data, model_id)

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
        return await _shared._record(
            self,
            handle=handle,
            action=action,
            target=target,
            result=result,
            detail=detail,
            before=before,
            after=after,
        )

    # ------------------------------------------------------------------
    # 拼装
    # ------------------------------------------------------------------
    def _item(
        self, model: AdminModel, usage: UsageWindow, ledger: UsageWindow | None = None
    ) -> dict:
        return _shared._item(model, usage, ledger)
