"""浏览器推送的订阅接口：拿公钥、登记、退订。

三个接口都只服务调用者自己：订阅是「我这个浏览器答应接收推送」，没有代别人订阅这
回事，所以没有任何一个参数说得出收件人是谁 —— 收件人就是带着令牌的那个人。

鉴权走 `require_auth_user`（要数字身份的那一族令牌）：订阅要落在一个真实的
`user.id` 上，而按 handle 认人的那一族没有 id。
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.response import ok
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.config import settings
from app.core.db import get_db
from app.domain.notification.push import PushSubscriptionRepository

router = APIRouter(prefix="/push", tags=["push"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


class PushSubscriptionIn(BaseModel):
    """`PushSubscription.toJSON()` 的形状，浏览器原样给出来的那份。"""

    endpoint: str = Field(min_length=1, max_length=2000)
    p256dh: str = Field(min_length=1, max_length=255)
    auth: str = Field(min_length=1, max_length=255)
    user_agent: str | None = Field(default=None, max_length=255)


class PushEndpointIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=2000)


@router.get("/key")
async def push_public_key() -> dict:
    """订阅要用的 VAPID 公钥；这个部署没开推送时 `key` 是 null。

    如实说不可用，而不是报错：前端拿到 null 就不去问权限 —— 问了也没有东西能发，
    而浏览器的推送权限被拒一次之后很难再问第二次。
    """
    return ok(
        {
            "key": settings.vapid_public_key if settings.web_push_configured else None,
            "available": settings.web_push_configured,
        }
    )


@router.put("/subscriptions")
async def save_subscription(
    body: PushSubscriptionIn,
    db: DbSession,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """登记（或更新）调用者这个浏览器的订阅。

    PUT 而不是 POST：同一个浏览器会反复订阅，每次换回同一个 `endpoint`，所以这是
    一次幂等的置入，不是每次新建一行。
    """
    await PushSubscriptionRepository(db).save(
        user_id=auth_user.user_id,
        endpoint=body.endpoint,
        p256dh=body.p256dh,
        auth=body.auth,
        user_agent=body.user_agent,
    )
    await db.commit()
    return ok({"saved": True})


@router.post("/subscriptions/delete")
async def drop_subscription(
    body: PushEndpointIn,
    db: DbSession,
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    """退订调用者自己的这个浏览器。

    带 body 的删除走 POST：`endpoint` 是一个上千字符的 URL，放进查询串会撞上代理和
    网关的行长限制，而 DELETE 带 body 在这条链路上不是每一层都转发。

    删除按 `(endpoint, user_id)` 两个条件：endpoint 本身唯一，但它不是秘密 —— 只按
    它删，等于任何登录用户拿到别人的 endpoint 就能替他关掉推送。
    """
    dropped = await PushSubscriptionRepository(db).drop(
        body.endpoint, user_id=auth_user.user_id
    )
    await db.commit()
    return ok({"deleted": dropped})
