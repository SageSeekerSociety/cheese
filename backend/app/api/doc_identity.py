"""Document-operation ownership uses a verified, live account's stable id."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationRequiredError
from app.domain.identity.actor import Actor
from app.domain.user.services import user_by_handle


async def operation_actor(db: AsyncSession, actor: Actor) -> str:
    if not actor.authenticated:
        raise AuthenticationRequiredError("操作回执需要已认证的写入者")
    user = await user_by_handle(db, actor.handle)
    if user is None or (actor.user_id is not None and actor.user_id != user.id):
        raise AuthenticationRequiredError("写入者账号已失效")
    return f"user:{user.id}"
