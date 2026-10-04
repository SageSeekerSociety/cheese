"""Document-operation ownership uses a verified, live account's stable id."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AuthenticationRequiredError, ForbiddenError
from app.core.sentences import say
from app.domain.identity.actor import Actor
from app.domain.identity.services import IdentityService
from app.domain.user.services import user_by_handle


async def operation_actor(db: AsyncSession, actor: Actor) -> str:
    if not actor.authenticated:
        raise AuthenticationRequiredError(say("docReceiptNeedsWriter"))
    user = await user_by_handle(db, actor.handle)
    if user is None or (actor.user_id is not None and actor.user_id != user.id):
        raise AuthenticationRequiredError(say("docWriterGone"))
    return f"user:{user.id}"


async def human_operation_actor(db: AsyncSession, actor: Actor) -> str:
    if actor.via != "token":
        raise AuthenticationRequiredError(say("docActionNeedsPerson"))
    identity = await operation_actor(db, actor)
    if await IdentityService(db).is_agent(actor.handle):
        raise ForbiddenError(say("docActionNotForAi"))
    return identity
