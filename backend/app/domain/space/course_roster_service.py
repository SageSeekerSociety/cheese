"""课程里的人：这门课有哪些学生、他们各自的项目、谁和谁一组。

课程模板的「学生与分组」（教师）那一屏的数据。三样东西全部来自别处已有的
记录，这一格只是把它们按**一门课**摆到一起，不新造任何一份数据：

- ``space_member`` 说谁在这门课里（#1295：加入即成员）；
- 项目（锚在这版某道赛题上）说每人在做什么 —— 一个学生一学期一个项目；
- 小队（``team``）说谁和谁一起，项目通过 ``Project.team_id`` 与它对上。

**跨领域只走 service。** 项目取 ``ProjectService``、小队取 ``team_service`` 工厂，
不碰任何一个别的领域的 repository —— 「repository 是领域内部的」这条线由
``tests/unit/test_domain_import_guard.py`` 守着，它是这一格唯一真正要小心的地方。

**谁能看这一格，不在这里判。** 名单把全班的人与项目列在一张表上，所以路由那侧
只对本版管理员（教师）开门，判据是 ``app.auth.space_access.is_space_admin`` ——
与打分、发题、读项目对话同一个答案，这里不另写一份。

本文件只做结构组装：返回的是 **user id 与 handle**，人味（昵称 / 头像）由路由那边
现有的 ``_hydrate_people`` 补齐，免得这个领域认识 user 领域的 repository。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.project.services import ProjectService
from app.domain.space.repositories import SpaceMemberRepository
from app.domain.team.services import team_service


class CourseRosterService:
    """一门课的人、他们的项目、他们的组。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def my_group(self, space_id: int, handle: str | None) -> dict[str, Any]:
        """学生自己视角的那一行：我在这门课里的项目、我挂在哪个组上。

        学生看不到花名册（那要教师），但他至少要知道自己在哪个组、组里还有谁 ——
        这两个问题的答案都只关于他自己，所以按 handle 过滤，不用管理员那道门。
        """
        if not handle:
            return {"teamId": None, "projectId": None}
        projects = [
            project
            for project in await ProjectService(self._session).list_for_space(space_id)
            if project.owner_handle == handle
        ]
        team_id = next((p.team_id for p in projects if p.team_id is not None), None)
        return {
            "teamId": team_id,
            "projectId": str(projects[0].id) if projects else None,
        }

    async def roster(self, space_id: int) -> dict[str, Any]:
        """这门课的结构：成员、项目、小队，各是一串平表。

        三张表分开返回而不是在这里拼成「学生 → 项目」，是因为拼它要人的 handle
        而 handle 在 user 领域 —— 那一跳由路由用 ``_hydrate_people`` 走完。
        """
        members = await SpaceMemberRepository(self._session).list_members(space_id)
        projects = await ProjectService(self._session).list_for_space(space_id)

        teams = team_service(self._session)
        team_ids = sorted({p.team_id for p in projects if p.team_id is not None})
        team_rows = await teams.get_teams_by_ids(team_ids)
        team_members: dict[int, list[int]] = {}
        for team_id in team_ids:
            relations = await teams.get_team_members(team_id)
            team_members[team_id] = [rel.user_id for rel in relations]

        return {
            "memberUserIds": [member.user_id for member in members],
            "projects": [
                {
                    "id": str(project.id),
                    "name": project.name,
                    "ownerHandle": project.owner_handle,
                    "teamId": project.team_id,
                }
                for project in projects
            ],
            "teams": [
                {
                    "id": team_id,
                    "name": team_rows[team_id].name,
                    "memberUserIds": team_members.get(team_id, []),
                }
                for team_id in team_ids
                if team_id in team_rows
            ],
        }
