"""后台的网关模型管理页 —— 九条路由，服务是 `GatewayModelsService`。

管理后台的第五块（前四块在 `admin_members.py` / `admin_feedback.py` / `admin_stats.py` /
`admin_spaces.py`），管的还是平台自己的东西：哪些模型在上架、各自烧了多少、项目的刹车值
设在哪。这些字在反馈功能删掉之后仍然成立，所以门是共用的 `PlatformAdminDep`，
服务是 `domain/agent/gateway_models.py`，两者都不从反馈那边借。

**失败要看得见**：这一页的动作是人点出来的，网关不可达、网关拒绝了这次写、想改的是
config.yaml 里的模型，都得在页面上有一句能读的原因。分两处翻，因为两处各答得上一半：

- 连接性失败由**这一层**翻 —— `GatewayAdmin`（会话路径那个「会抛」的客户端）已经把
  「连不上」和「被拒」分成了两类，路由只把它映成 HTTP 语义：不可达 503、被拒 502。
  这两种语义不分家的话，页面就没法告诉人「是网关挂了，过会儿再试」还是「你的动作本身
  不被允许，要改的是那条模型」。
- 「模型不存在」404、「请求体不合法 / 不变式被违反 / 对 config 模型写」400 由**服务层**
  抛仓库既有的 `NotFoundError` / `BadRequestError`：那几种要读库或网关才知道，路由层
  没有判断权，也就不该有翻译权（同 `admin_members.py`「删不删得掉由服务端说了算」）。

`days` 的上下界照 `admin_stats.py` 的纪律写死在签名上（`ge=1` / `le=90`）：读用量接口
有成本，且 0 天的窗口画不出一条折线。`limit` 同理（`ge=1` / `le=200`）。

一个模块一个 router（`main.py` 的自动发现），所以九个端点都在这一个文件里。
"""

import uuid
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from starlette.status import HTTP_502_BAD_GATEWAY

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.core.config import settings
from app.core.errors import BaseError, GatewayUnavailableError
from app.domain.agent.gateway_admin import (
    GatewayAdmin,
    GatewayRefused,
    GatewayUnreachable,
)
from app.domain.agent.gateway_models import GatewayModelsService
from app.domain.agent.schemas import (
    BlockedUpdate,
    BudgetUpdate,
    ModelCreate,
    ModelUpdate,
)

router = APIRouter(prefix="/admin/gateway", tags=["admin"])


def get_gateway_admin() -> GatewayAdmin | None:
    """管理 API 的「会抛」客户端；这个部署没配管理凭据时给 None。

    None 是一个支持的部署形态而不是错误（同 `deps.get_llm_gateway`）：页面照常打开，
    只是模型列表标成不可用、写操作无处可去。判断留在这里而不是服务里，是因为「有没有配
    管理凭据」是配置问题，服务只该拿到一个客户端、或者一个明确的 None。

    不缓存：`GatewayAdmin` 只是个 base + header 的壳，构造它没有任何成本，而少一层
    `lru_cache` 让测试能换掉它而不必去戳 settings（集成测试用它打桩网关）。
    """
    if settings.llm_gateway_admin_base and settings.llm_gateway_admin_key:
        return GatewayAdmin(
            settings.llm_gateway_admin_base, settings.llm_gateway_admin_key
        )
    return None


GatewayAdminDep = Annotated[GatewayAdmin | None, Depends(get_gateway_admin)]


async def get_gateway_models_service(
    db: DbSession, admin: GatewayAdminDep
) -> GatewayModelsService:
    return GatewayModelsService(db, admin)


ModelsServiceDep = Annotated[GatewayModelsService, Depends(get_gateway_models_service)]


async def _answered(awaitable: Awaitable[dict]) -> dict:
    """跑一次服务调用，把网关的连接性失败翻成对外的 HTTP 语义。

    503 = 网关没答话（`GatewayUnreachable`，过会儿重试有意义）；502 = 网关答了但拒绝了
    （`GatewayRefused`，要改的是那条模型本身）。两个都带上原始的中文原因，页面原样显示。

    `errors.py` 里没有 502 的具名类型，就用 `BaseError`；它和具名子类走同一条
    `base_error_handler`（`error.name` 会是 `BaseError`，`error.message` 是那句话）——
    现状如此，不值得为这一处新造子类。
    """
    try:
        return await awaitable
    except GatewayUnreachable as exc:
        raise GatewayUnavailableError(str(exc)) from exc
    except GatewayRefused as exc:
        raise BaseError(HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ---- 模型 ----


@router.get("/models")
async def list_models(
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """模型那一张表：网关当下这一眼的每条模型，各自配上窗口内的用量。

    形状钉死在契约 §3.1（`gateway` / `window` / `totals` / `models`），前端按它写。
    `handle` 这一层用不到，但它必须出现 —— 门是在签名上过的（`PlatformAdminDep`）。
    """
    return ok(await _answered(service.listing(days=days)))


@router.get("/models/{name}")
async def model_detail(
    name: str,
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """一条模型的详情：列表那一项 + 逐日折线（契约 §3.2）。

    未知模型由服务层抛 `NotFoundError`（404），这里不判 —— 判它要读网关。
    """
    return ok(await _answered(service.detail(name=name, days=days)))


@router.post("/models")
async def create_model(
    payload: ModelCreate,
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """新建一条运行时模型，回它的详情（契约 §3.3）。

    `handle` 传给服务当审计的操作人：写操作要在 `gateway_admin_audit` 落一行「谁干的」。
    上架不变式（`selectable` 必须有输入与输出两个单价）由服务层守，违反时它抛 400。
    """
    return ok(await _answered(service.add(handle=handle, payload=payload)))


@router.patch("/models/{name}")
async def update_model(
    name: str,
    payload: ModelUpdate,
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """改一条运行时模型，回更新后的详情（与新建同一个形状，契约 §3.3）。

    名字以路径为准；config 来源的模型到这里会被服务层以 400 拒掉 —— 网关不支持改它，
    要改得动 `deploy/gateway/config.yaml` 再发布网关。
    """
    return ok(
        await _answered(service.update(handle=handle, name=name, payload=payload))
    )


@router.delete("/models/{name}")
async def delete_model(
    name: str,
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """删一条运行时模型，回 `{"deleted": true}`（契约 §2.3）。"""
    return ok(await _answered(service.delete(handle=handle, name=name)))


@router.post("/models/{name}/blocked")
async def set_model_blocked(
    name: str,
    payload: BlockedUpdate,
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """停用或启用一条运行时模型，回 `{"blocked": bool}`（契约 §2.3）。

    停用是让网关不再路由它、选择器里也不再出现（`GatewayModel.selectable` 把 `blocked`
    算进去了）；config 来源的模型同样在这里被服务层拒绝。
    """
    return ok(
        await _answered(
            service.set_blocked(handle=handle, name=name, blocked=payload.blocked)
        )
    )


# ---- 项目额度 ----


@router.get("/projects")
async def list_projects(
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
    days: int = Query(default=7, ge=1, le=90),
) -> dict:
    """项目那一张表：每个项目的额度与用量（契约 §3.4）。

    按模型取用量一律来自网关的 `model_groups`，不碰平台库的 `resource_usage.by_model`
    —— 后者对网关流量的口径已知有问题（网关把 `model` 写成了部署默认模型）。
    """
    return ok(await _answered(service.projects(days=days)))


@router.put("/projects/{project_id}/budget")
async def set_project_budget(
    project_id: uuid.UUID,
    payload: BudgetUpdate,
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """设或清一个项目的刹车值，立即落到网关的 key 上，回同一项（契约 §3.4）。

    `max_budget_usd=null` 是清空这道刹车。`project_id` 交给 FastAPI 解成 `uuid.UUID`，
    形状不对的直接 400，进不了服务层。
    """
    return ok(
        await _answered(
            service.set_budget(
                handle=handle,
                project_id=project_id,
                max_budget_usd=payload.max_budget_usd,
            )
        )
    )


# ---- 审计 ----


@router.get("/audit")
async def audit_log(
    service: ModelsServiceDep,
    handle: PlatformAdminDep,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """最近 N 条后台操作（契约 §3.5），按时间倒序。

    成功的写和失败的写都在里面（失败的 `result="failed"` 并带一句 `detail`）—— 失败也是
    要有人看见的事实，正是这一页存在的理由之一。
    """
    return ok(await _answered(service.audit(limit=limit)))
