"""一个小队开的项目，队里的人默认就是项目成员。

项目本来就归小队（项目归团队 v4）。要队里的人一个一个再被邀请一遍，等于把「我们
是一个队」这件事重说一次。

这一份同时钉住一条**不该**做的：建项目的人不写进成员表。这个仓里「谁是所有者」
记在 Project.owner_handle 上，成员表存的是其他人；把他也塞进去，「把所有者加进
名册」这个动作本身就会插重复键（实测炸了 spool 和 sandbox 两组用例）。
"""

from app.domain.project.services import ProjectService
from app.domain.team.services import team_service

OWNER = "owner-1"


def _roster(client, project_id: str) -> dict[str, str]:
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    return {m["user_handle"]: m["role"] for m in rows}


def test_the_person_who_made_it_is_not_a_member_row(client):
    """所有者不进成员表——他在 Project.owner_handle 上。

    界面上他当然要出现（成员页自己补那一行），但那是界面的事：这里多插一行，会让
    别处「把所有者加进名册」的调用撞上唯一约束（实测炸了 spool 和 sandbox 两组
    用例）。
    """
    pid = client.post("/projects", json={"name": "P", "owner_handle": OWNER}).json()[
        "data"
    ]["id"]
    assert _roster(client, pid) == {}


async def _user(factory, username: str) -> int:
    """一个真的知是用户，返回它的 int id。小队成员是按 int id 记的。"""
    from datetime import UTC, datetime

    from app.domain.user.models import User

    now = datetime.now(UTC)
    async with factory() as session:
        user = User(
            username=username,
            email=f"{username}@example.com",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        uid = user.id
        await session.commit()
    return uid


async def test_a_teams_project_starts_with_the_whole_team(client):
    """一个小队开的项目，队里的人不用再被邀请一遍。"""
    from app.domain.team.models import TeamMemberRole
    from app.domain.team.repositories import TeamRepository

    factory = client.test_factory  # type: ignore[attr-defined]
    captain = await _user(factory, "captain")
    mate = await _user(factory, "teammate")

    async with factory() as session:
        team = await team_service(session).create_team(
            name="小队", intro="", description="", avatar_id=1, owner_id=captain
        )
        await TeamRepository(session).add_member(team.id, mate, TeamMemberRole.MEMBER)
        team_id = team.id
        await session.commit()

    async with factory() as session:
        project = await ProjectService(session).create(
            name="P", owner_handle="captain", team_id=team_id
        )
        pid = str(project.id)
        await session.commit()

    roster = _roster(client, pid)
    # 队友进来了；建项目的那个人不在这张表上（他在 owner_handle 上）。
    assert roster == {"teammate": "member"}


async def test_a_personal_project_pulls_nobody_in(client):
    """个人项目落在个人小队上，那里只有建项目的人自己——所以这条规则在那儿什么也
    不做，不会捎带任何别人进来。"""
    factory = client.test_factory  # type: ignore[attr-defined]
    await _user(factory, "solo")

    async with factory() as session:
        project = await ProjectService(session).create(name="P", owner_handle="solo")
        pid = str(project.id)
        await session.commit()

    assert _roster(client, pid) == {}
