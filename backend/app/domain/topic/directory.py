"""A project's channels as one person may know them: what 「全部频道」 lists
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

from app.auth.project_roles import may
from app.domain.project_permissions import Permission
from app.domain.topic.models import TopicKind, TopicStatus
from app.domain.topic.ports import OpenTaskCounts, ProjectTeam, TopicMembership
from app.domain.topic.repositories import TopicRepository


async def channel_directory(
    session: AsyncSession,
    project_id: uuid.UUID,
    viewer: str,
    *,
    team: ProjectTeam,
    counts: OpenTaskCounts,
    members: TopicMembership,
) -> dict:
    repo = TopicRepository(session)
    topics = await repo.list_for_project(project_id)
    seen = {t.id for t in await members.seen(topics, viewer, team=team)}
    manages_project = await may(session, project_id, viewer, Permission.manage_channels)
    listed = [t for t in topics if t.id in seen or manages_project]
    ids = [t.id for t in listed]
    # 每个频道的主人一次取完：这里逐频道问 `owner_of` 是整页唯一还在随频道数
    # 增长的读（下面那些全是批量接口），而一个项目几十个频道就是几十次往返。
    # 先跑 `owners_of` 会把所有频道的 `topic_memberships` 一次记进 `request_facts`，
    # 下面的 `joined_topic_ids`、`seat_counts`、`managed_topic_ids` 直接命中内存。
    managers = await members.owners_of(ids)
    joined = await members.joined_topic_ids(listed, viewer, team=team)
    seats = await members.seat_counts(listed, team=team)
    # 频道卡片上「N 个进行中的任务」那一个数：和侧栏那一行的 `my_tasks.open`、项目
    # 总览是同一个判据（`underway_counts`）——合并后在写总结的那几个不算，因为它们
    # 已经在收尾、没有人还要动。曾经这里读的是 `open_counts`（只按 status 数），于是
    # 同一个频道在三处会给出不同的数（清单第 14 条）。字段名沿用 `open_tasks`，值换成
    # 这一个判据。
    open_tasks = await counts.underway_counts(ids)
    last_activity = await repo.last_activity_for_topics(ids)
    managed = await members.managed_topic_ids(
        [t for t in listed if t.id in seen], viewer
    )
    items = []
    for t in listed:
        general = t.kind == TopicKind.root
        visible = t.id in seen
        manager = None if general else managers.get(t.id)
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
