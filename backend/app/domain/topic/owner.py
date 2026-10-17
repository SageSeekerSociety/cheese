"""Who owns a newborn place: 一条从人到团队的四档梯子。

两处问它：一个房间从没人的地方开出来（`TopicService.create`），和一条活从一条
消息升起来（`room_task.dispatch.create_task`）。后者的那个包在本包之下，读它是
向下的，所以梯子留在这里，两边都来取 —— 两次问的必须是同一个答案，不然一个刚开
出来的房间，不同的人看到不同的主人。

名字不带下划线：它现在跨包用（`app.domain.room_task.dispatch`），不再是本包的
私事。
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.identity.handles import looks_like_agent_handle
from app.domain.team.models import TeamMemberRole
from app.domain.team.services import team_service
from app.domain.topic.models import TopicRole
from app.domain.topic_membership.services import TopicMemberService
from app.domain.user.services import usernames_by_ids


async def resolve_owner(
    session: AsyncSession,
    created_by: str | None,
    *,
    parent_id: uuid.UUID | None,
    project_owner: str | None,
    project_team: int | None,
) -> str | None:
    """Who owns a newborn topic: the real human who created it, else the
    parent room's owner, else the project's owner, else its team's owner.

    That last rung is not decoration. Measured on the dogfooding project
    2026-08-12, answering 「新话题的拥有者为什么有的有，有的是空的」: the
    project's ``owner_handle`` is NULL and its root topic is ownerless too,
    so every topic 芝士 opened under the root fell through all three rungs
    and came out blank — five active rooms with no one able to manage the
    roster. The ladder was right; its bottom had nothing to stand on. The
    project's team does: its owner is "who is in charge here" already
    recorded, not a new policy invented to fill a hole.

    Returns None only when the team has no owner either — the caller still
    seeds 芝士, and the room stays manageable by any project member.

    "Is the creator 芝士" spans the whole agent handle namespace, not the bare
    ``cheese`` string: each 分身 creates under its own ``cheese-<topic hex>``
    handle, and a string match would make the 分身 the room's owner — the one
    thing this chain exists to prevent (same rule as dispatch_task).

    ``project_owner`` 和 ``project_team`` 都是项目那一行上的两个字段，由调用方
    递进来：本包在第 2 层、`project` 在第 3 层，读它是向上的。两个调用方都已经把
    项目读在手上了（`TopicService.create`、`room_task.dispatch.create_task`），
    多递一个 ``team_id`` 不多一次查询。"""
    if created_by and not looks_like_agent_handle(created_by):
        return created_by
    members = TopicMemberService(session)
    if parent_id is not None:
        parent_members, _ = await members.list_for_topic(parent_id)
        parent_owner = next(
            (m.member_handle for m in parent_members if m.role == TopicRole.owner),
            None,
        )
        if parent_owner:
            return parent_owner
    if project_owner:
        return project_owner
    if project_team is None:
        return None
    return await _team_owner(session, project_team)


async def _team_owner(session: AsyncSession, team_id: int) -> str | None:
    """The owner of a team, as the last rung of the ladder.

    Read lazily — only when the rungs above came up empty — so an ordinary
    topic-create still costs no extra query.
    """
    owners = [
        r.user_id
        for r in await team_service(session).get_team_members(team_id)
        if r.role == TeamMemberRole.OWNER
    ]
    names = await usernames_by_ids(session, owners)
    return next((names[uid] for uid in owners if uid in names), None)
