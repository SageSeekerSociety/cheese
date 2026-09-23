"""后台的「订阅导入」—— ChatGPT 订阅的 device flow 与凭据生命周期。

模型管理页重设计的第三条来源（配置文件 / 运行时新增 / **订阅**）背后的七条
路由。服务是 `domain/subscription/services.py` 的 `SubscriptionService`，门是
共用的 `PlatformAdminDep`（同 `admin_models.py`：每个 handler 签名上挂它，
agent 由 `authz.policy` 在路由体内拒绝）。**不进 `_CHEESE_WRITE_PATHS`** ——
那张表是沙箱 agent 凭证的白名单，`/admin/*` 的授权在签名上完成。

失败语义照 `admin_models._answered` 的翻译表，两边各答得上一半：

- 连接性失败（OpenAI 授权服务 / 额度接口不可达、网关不可达）→ **503**，
  「过会儿再试有意义」。
- 上游答了话但拒绝（网关 4xx/5xx）→ **502**；订阅凭据被 OpenAI 判死
  （`SubscriptionTokenInvalid`）也 → **502**，页面把它显示成「要重新授权」。
- 「不存在 / 状态不对 / 检测到不同的账号」由服务层抛仓库既有的
  `NotFoundError` / `BadRequestError` / `ConflictError`（404/400/409）——
  那几种要读库才知道，路由层没有判断权。

凭据纪律：响应 DTO 在 `_dto` 里脱敏，token 与密文字段一个字母都不出现；
每一次状态转变（含失败）都在 `gateway_admin_audit` 落行，actor 是操作者
handle（后台自动刷新落 `system`，见 services.py 的约定）。
"""

import uuid
from collections.abc import Awaitable
from typing import Annotated

from fastapi import APIRouter, Depends
from starlette.status import HTTP_502_BAD_GATEWAY

from app.api.response import ok
from app.api.routes.admin_common import DbSession, PlatformAdminDep
from app.api.routes.admin_models import get_gateway_admin
from app.core.errors import BaseError, GatewayUnavailableError
from app.domain.agent.gateway_admin import (
    GatewayAdmin,
    GatewayRefused,
    GatewayUnreachable,
)
from app.domain.subscription.openai_codex import (
    OpenAICodexOAuth,
    SubscriptionTokenInvalid,
    SubscriptionUnreachable,
)
from app.domain.subscription.schemas import DeviceFlowStart
from app.domain.subscription.services import SubscriptionService

router = APIRouter(prefix="/admin/subscriptions", tags=["admin"])


def get_openai_oauth() -> OpenAICodexOAuth:
    """OpenAI 那一侧的客户端。测试经 `dependency_overrides` 换成
    MockTransport 的版本（与 `get_gateway_admin` 同一个打桩法）。"""
    return OpenAICodexOAuth()


OAuthDep = Annotated[OpenAICodexOAuth, Depends(get_openai_oauth)]

GatewayAdminDep = Annotated[GatewayAdmin | None, Depends(get_gateway_admin)]


async def get_subscription_service(
    db: DbSession, oauth: OAuthDep, admin: GatewayAdminDep
) -> SubscriptionService:
    return SubscriptionService(db, oauth, admin)


SubscriptionServiceDep = Annotated[
    SubscriptionService, Depends(get_subscription_service)
]


async def _answered(awaitable: Awaitable[dict]) -> dict:
    """跑一次服务调用，把连接性失败翻成对外的 HTTP 语义（对照表见文件头）。

    状态转变（含失败的那种）在抛错前已由服务落库 —— 这里的 503/502 只影响
    页面这次看到什么，不影响库里的真相。
    """
    try:
        return await awaitable
    except (GatewayUnreachable, SubscriptionUnreachable) as exc:
        raise GatewayUnavailableError(str(exc)) from exc
    except GatewayRefused as exc:
        raise BaseError(HTTP_502_BAD_GATEWAY, str(exc)) from exc
    except SubscriptionTokenInvalid as exc:
        raise BaseError(HTTP_502_BAD_GATEWAY, str(exc)) from exc


# ---- device flow（先声明，免得被 /{subscription_id} 形的路由吃掉）----


@router.post("/device-flows")
async def start_device_flow(
    payload: DeviceFlowStart,
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """开一次导入（或定向重授权）：回 user_code 与轮询句柄。

    「一座一订阅」由服务层兑现：同一来源已有活跃订阅时 409（先移除，或从它
    发起重新授权）；`target_subscription_id` 非空即定向重授权，完成时服务端
    校验同一身份，不同账号 400。
    """
    return ok(
        await _answered(
            service.start_flow(
                handle=handle,
                provider=payload.provider,
                label=payload.label,
                target_id=payload.target_subscription_id,
            )
        )
    )


@router.post("/device-flows/{flow_id}/poll")
async def poll_device_flow(
    flow_id: uuid.UUID,
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """前端轮询一次：`pending` / `complete`（带订阅 DTO）/ `expired`。

    404 = 这个 flow 不存在或已结束。完成那一步的推送网关失败照样落库落审计，
    这里把网关的原话带出来（503/502）。
    """
    return ok(await _answered(service.poll_flow(handle=handle, flow_id=flow_id)))


@router.post("/device-flows/{flow_id}/cancel")
async def cancel_device_flow(
    flow_id: uuid.UUID,
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """取消一次进行中的授权：置 superseded，落 `subscription.cancel` 审计。"""
    return ok(await _answered(service.cancel_flow(handle=handle, flow_id=flow_id)))


# ---- 订阅本身 ----


@router.get("")
async def list_subscriptions(
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """全部订阅（按创建时间倒序），DTO 脱敏 —— token 一个字母都不出现。"""
    return ok(await _answered(service.list()))


@router.post("/{subscription_id}/refresh")
async def refresh_subscription(
    subscription_id: uuid.UUID,
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """手动刷新一次并推进网关。凭据被判死时回 `reauth_required` 状态（不是
    报错 —— 那是一个状态）；连接性失败 503。"""
    return ok(
        await _answered(
            service.refresh_now(handle=handle, subscription_id=subscription_id)
        )
    )


@router.get("/{subscription_id}/quota")
async def subscription_quota(
    subscription_id: uuid.UUID,
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """按需查一次额度。传输错误回旧快照（`stale: true`），没有旧快照才 503；
    凭据被判死 → 502「凭据已失效，需要重新授权」。"""
    return ok(
        await _answered(
            service.fetch_quota(handle=handle, subscription_id=subscription_id)
        )
    )


@router.delete("/{subscription_id}")
async def revoke_subscription(
    subscription_id: uuid.UUID,
    service: SubscriptionServiceDep,
    handle: PlatformAdminDep,
) -> dict:
    """移除一条订阅：置 revoked，并 best-effort 停用挂在网关上的模型。

    断开订阅是第一诉求：网关那一下失败，订阅照样是终态（已落库），这里把
    网关的原话带出来（503/502），审计落 failed。
    """
    return ok(
        await _answered(
            service.revoke(handle=handle, subscription_id=subscription_id)
        )
    )
