"""平台自己项目里的房间门禁：给做这个平台本身的 AI 队友开的只读口共用。"""

import uuid

from app.api.auth import ActorResolverDep
from app.api.request_deps import DbSession
from app.core.errors import ForbiddenError
from app.core.sentences import say
from app.domain.feedback.claims import is_platform_project
from app.domain.room_task.place import place_or_404


async def require_platform_room(
    db: DbSession, resolver: ActorResolverDep, topic: uuid.UUID
) -> None:
    """The caller is in this room, and the room's project works on the platform."""
    place = await place_or_404(db, topic)
    who = await resolver.resolve(
        topic_id=place.conversation_id, project_id=place.project_id
    )
    await resolver.authorize_topic(
        who, project_id=place.project_id, topic_id=place.room_id, enforce=True
    )
    if not await is_platform_project(db, place.project_id):
        raise ForbiddenError(say("runRecordsPlatformRoomsOnly"))
