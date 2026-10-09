"""A person's own marks on a conversation: where they last read it, and how
loudly it may call them.

Both are per person, and whose they are comes from the verified credential.
The module mounts itself: `app.main._discover_routers` includes every
module-level `APIRouter` under `app.api.routes`.
"""

import uuid

from fastapi import APIRouter

from app.api.auth import ActorResolverDep
from app.api.response import ok
from app.api.routes.topics import DbSession, _parse_moment
from app.domain.conversation.services import room_of
from app.domain.topic.services import TopicService

router = APIRouter(prefix="/topics", tags=["topics"])


@router.post("/{topic_id}/read")
async def mark_topic_read(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """已读位: bump the caller's read cursor on a room or on one of its tasks
    (opening either clears its unread badge, Feishu-style). ``topic_id`` is
    the conversation's id; the door is its room's.

    The cursor is per person, so whose it is comes from the verified
    credential — ``handle`` in the body is only an assertion checked against
    it (it used to BE the identity, letting anyone move anyone's cursor)."""
    room_id = await room_of(db, topic_id)
    topic = await TopicService(db).get_or_404(room_id)
    actor = await resolver.resolve(topic_id=room_id, project_id=topic.project_id)
    await resolver.authorize_topic(actor, project_id=topic.project_id, topic_id=room_id)
    handle = await resolver.resolve_recipient(
        requested=(body.get("handle") or "").strip() or None,
        project_id=topic.project_id,
        allow_anonymous=False,
    )
    await TopicService(db).mark_read(topic_id, handle)
    return ok({"topic_id": str(topic_id), "handle": handle})


@router.put("/{topic_id}/notify-level")
async def set_topic_notify_level(
    topic_id: uuid.UUID, body: dict, db: DbSession, resolver: ActorResolverDep
) -> dict:
    """这个频道对我的通知档位（`NotifyLevel`）；静音可以带 ``muted_until``（ISO
    时间），到点算回默认。和已读位一样按人记，人是谁取自已验证的凭据。"""
    topic = await TopicService(db).get_or_404(topic_id)
    actor = await resolver.resolve(topic_id=topic_id, project_id=topic.project_id)
    await resolver.authorize_topic(
        actor, project_id=topic.project_id, topic_id=topic_id
    )
    handle = await resolver.resolve_recipient(
        requested=None, project_id=topic.project_id, allow_anonymous=False
    )
    level = str(body.get("level") or "")
    until = _parse_moment(body.get("muted_until"))
    await TopicService(db).set_notify_level(topic_id, handle, level, until)
    return ok(
        {
            "topic_id": str(topic_id),
            "level": level,
            "muted_until": until.isoformat() if until else None,
        }
    )
