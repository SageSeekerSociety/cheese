"""A project's channels as one person may know them: what 「浏览频道」 lists
for everyone, and the channels page of the project's settings for whoever
manages the project.

Everyone gets the public channels and the private ones they are in, with what
each is for, how many are in it, its open tasks and when it was last active.
Whoever manages the project also gets the private channels they are not in,
by name, manager, size and last activity only: what is said and done in one
stays with its people.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.membership.services import MemberService
from app.domain.room_task.services import TaskService
from app.domain.topic.models import TopicKind, TopicStatus
from app.domain.topic.repositories import TopicRepository
from app.domain.topic_membership.services import TopicMemberService


async def channel_directory(
    session: AsyncSession, project_id: uuid.UUID, viewer: str
) -> dict:
    members = TopicMemberService(session)
    repo = TopicRepository(session)
    topics = await repo.list_for_project(project_id)
    seen = {t.id for t in await members.seen(topics, viewer)}
    manages_project = await MemberService(session).manages(project_id, viewer)
    listed = [t for t in topics if t.id in seen or manages_project]
    ids = [t.id for t in listed]
    joined = await members.joined_topic_ids(listed, viewer)
    seats = await members.seat_counts(listed)
    open_tasks = await TaskService(session).open_counts(ids)
    last_activity = await repo.last_activity_for_topics(ids)
    managed = await members.managed_topic_ids(
        [t for t in listed if t.id in seen], viewer
    )
    items = []
    for t in listed:
        general = t.kind == TopicKind.root
        visible = t.id in seen
        manager = None if general else await members.owner_of(t.id)
        when = last_activity.get(t.id)
        items.append(
            {
                "id": str(t.id),
                "title": t.title,
                "general": general,
                "members_only": t.members_only,
                "archived": t.status == TopicStatus.archived,
                "joined": t.id in joined,
                "visible": visible,
                "description": t.description if visible else None,
                "member_count": seats.get(t.id, 0),
                "open_tasks": open_tasks.get(t.id, 0) if visible else None,
                "last_activity_at": when.isoformat() if when else None,
                "manager": manager,
                "can_manage": t.id in managed,
                "can_administer": not general
                and (manages_project or manager == viewer),
            }
        )
    return {"items": items, "manages_project": manages_project}
