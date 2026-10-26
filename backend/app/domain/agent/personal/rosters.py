"""Who is in a team or a space, written out for a person's 芝士.

What 芝士 reads before it manages people for the person: the teams they are
in and their role in each, a team's members, a space's managers and members.
Each names people by their name and handle, and each team or space by the ref
that acts on it (``teams/9``, ``spaces/4``). Only what the person may see on
the site: a team they are in, a space they are in or run.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.space_access import is_space_admin, space_admin_ids
from app.core.errors import NotFoundError
from app.domain.space.services import SpaceLabels, SpaceService
from app.domain.team.services import team_service
from app.domain.user.services import names_by_handles, usernames_by_ids

#: A team role, by the int the team domain stores.
_TEAM_ROLE = {0: "所有者", 1: "管理员", 2: "成员"}
#: The most people one read lists; it says how many more there are.
LISTED = 100


async def _named(session: AsyncSession, user_ids: Sequence[int]) -> dict[int, str]:
    """「赵一鸣（zhaoym）」 for each id, the way 芝士 names people to act on."""
    handles = await usernames_by_ids(session, user_ids)
    shown = await names_by_handles(session, list(handles.values()))
    named: dict[int, str] = {}
    for user_id, handle in handles.items():
        name = shown.get(handle) or handle
        named[user_id] = f"{name}（{handle}）" if name != handle else handle
    return named


def _more(total: int) -> list[str]:
    return [f"……还有 {total - LISTED} 人"] if total > LISTED else []


async def my_teams(session: AsyncSession, user_id: int) -> str:
    """The teams the person is in, and their role in each."""
    service = team_service(session)
    lines: list[str] = []
    for team in await service.get_teams_of_user(user_id):
        held = next(
            (
                r
                for r in await service.get_team_members(team.id)
                if r.user_id == user_id
            ),
            None,
        )
        role = _TEAM_ROLE.get(held.role, "成员") if held is not None else "成员"
        lines.append(f"- 团队「{team.name}」（teams/{team.id}），你是{role}")
    return "\n".join(lines) if lines else "你不在任何团队里。"


async def team(session: AsyncSession, user_id: int, team_id: int) -> str:
    """A team the person is in: its members and their roles."""
    service = team_service(session)
    found = await service.get_team(team_id)
    if found is None or not await service.is_team_member(team_id, user_id):
        raise NotFoundError.for_resource("team", str(team_id))
    members = list(await service.get_team_members(team_id))
    named = await _named(session, [m.user_id for m in members[:LISTED]])
    lines = [f"团队「{found.name}」（teams/{team_id}），{len(members)} 人："]
    lines += [
        f"- {named.get(m.user_id, m.user_id)}，{_TEAM_ROLE.get(m.role, '成员')}"
        for m in members[:LISTED]
    ]
    return "\n".join(lines + _more(len(members)))


async def space(session: AsyncSession, user_id: int, space_id: int) -> str:
    """A space the person is in or runs: its managers and its members."""
    runs = await is_space_admin(session, space_id=space_id, user_id=user_id)
    spaces = SpaceService.for_routes(session)
    if not runs and not await spaces.is_member(space_id=space_id, user_id=user_id):
        raise NotFoundError.for_resource("space", str(space_id))
    label = (await SpaceLabels(session).describe([space_id])).get(space_id)
    managers = sorted(await space_admin_ids(session, space_id=space_id))
    members = [m.user_id for m in await spaces.list_members(space_id)]
    named = await _named(session, managers + members[:LISTED])
    name = label.name if label is not None else str(space_id)
    lines = [f"空间「{name}」（spaces/{space_id}）"]
    lines.append("管理员：" + "、".join(named.get(i, str(i)) for i in managers))
    lines.append(f"成员（{len(members)} 人）：")
    lines += [f"- {named.get(i, str(i))}" for i in members[:LISTED]]
    return "\n".join(lines + _more(len(members)))
