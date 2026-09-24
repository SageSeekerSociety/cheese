"""名册上有谁：成员表里的人、小队里的人，加上项目的所有者。

项目本来就归小队（项目归团队 v4）。要队里的人一个一个再被邀请一遍，等于把「我们
是一个队」这件事重说一次。

所有者这一行是**读的时候补出来的**，不是成员表里的记录：这个仓里「谁是所有者」记
在 Project.owner_handle 上，成员表存的是其他人；把他也插进那张表，「把所有者加进
名册」这个动作本身就会撞唯一约束（实测炸了 spool 和 sandbox 两组用例）。补出来这
一行不能省——名册是聊天面板把 handle 换成昵称和头像的唯一依据，也是 @ 谁算数、谁
收得到强提醒的准绳，而所有者往往正是那个项目里说话最多的人。
"""

import uuid
from datetime import UTC, datetime

from app.domain.membership.repositories import MemberRepository
from app.domain.project.repositories import ProjectRepository
from app.domain.project.services import ProjectService
from app.domain.team.services import team_service
from tests.integration.conftest import post_project, registered

OWNER = "owner-1"


def _people(client, project_id: str) -> list[dict]:
    """名册上**人**那些行。

    名册上也有这个项目的队友（一张名册，队友也在上面）；本文件问的是人那一半从哪
    三处来，所以这里把队友滤掉。队友在不在名册上，由
    ``test_one_roster_agents_included`` 守。
    """
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    return [m for m in rows if not m["agent"]]


def _roster(client, project_id: str) -> dict[str, str]:
    """handle → how they are on the roster (owner / team / external)."""
    return {m["user_handle"]: m["source"] for m in _people(client, project_id)}


def _rows(client, project_id: str) -> dict[str, dict]:
    return {m["user_handle"]: m for m in _people(client, project_id)}


def test_the_owner_is_on_the_roster_without_a_member_row(client):
    """所有者出现在名册上，同时成员表里没有他那一行。

    两件事得同时成立：界面和 @ 都读名册，所以他必须在上面；而成员表里多这一行会
    让别处「把所有者加进名册」的调用撞上唯一约束。
    """
    pid = post_project(client, json={"name": "P", "owner_handle": OWNER}).json()[
        "data"
    ]["id"]

    assert _roster(client, pid) == {OWNER: "owner"}

    async def absent() -> bool:
        async with client.test_request_factory() as session:
            return (
                await MemberRepository(session).get(project_id=pid, user_handle=OWNER)
                is None
            )

    assert client.portal.call(absent)


def test_the_owner_is_shown_by_nickname_and_chosen_avatar(client):
    """所有者那一行带昵称和头像，跟名册上任何人一样。

    聊天面板只拿得到发言人的 handle，昵称和头像都得从这张名册里查。查不到就退回
    handle 原文和彩色首字母 —— 这正是所有者以前在自己项目里显示成一串英文 id 的
    原因。
    """
    from app.domain.avatars.models import Avatar
    from app.domain.user.models import User, UserProfile

    async def seed() -> int:
        async with client.test_request_factory() as session:
            now = datetime.now(UTC)
            avatar = Avatar(
                url="",
                name="face.png",
                avatar_type="upload",
                created_at=now,
                usage_count=0,
            )
            session.add(avatar)
            await session.flush()
            user = User(
                username="boss",
                email="boss@example.com",
                created_at=now,
                updated_at=now,
            )
            session.add(user)
            await session.flush()
            session.add(
                UserProfile(
                    user_id=user.id,
                    nickname="老板",
                    intro="",
                    avatar_id=avatar.id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()
            return avatar.id

    avatar_id = client.portal.call(seed)

    pid = post_project(client, json={"name": "P", "owner_handle": "boss"}).json()[
        "data"
    ]["id"]

    row = _rows(client, pid)["boss"]
    assert row["name"] == "老板"
    assert row["avatar_id"] == avatar_id


def test_an_owner_who_also_has_a_member_row_appears_once(client):
    """所有者在成员表里也有一行时（旧数据留下的），名册上仍然只有他一行。

    重复一行会让界面上出现两个同名的人，也会让 @ 的通知发两遍。现在接口不会再写
    出这种行，所以直接写库造出来。
    """
    pid = post_project(client, json={"name": "P", "owner_handle": OWNER}).json()[
        "data"
    ]["id"]

    async def legacy_row() -> None:
        async with client.test_request_factory() as session:
            await MemberRepository(session).add(
                project_id=uuid.UUID(pid), user_handle=OWNER
            )
            await session.commit()

    client.portal.call(legacy_row)

    rows = _people(client, pid)
    assert [m["user_handle"] for m in rows] == [OWNER]
    assert rows[0]["source"] == "owner"


async def _user(factory, username: str) -> int:
    """一个真的知是用户，返回它的 int id。小队成员是按 int id 记的。"""
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


def test_a_teams_project_starts_with_the_whole_team(client):
    """一个小队开的项目，队里的人不用再被邀请一遍。"""
    from app.domain.team.models import TeamMemberRole
    from app.domain.team.repositories import TeamRepository

    async def seed() -> str:
        factory = client.test_request_factory
        captain = await _user(factory, "captain")
        mate = await _user(factory, "teammate")
        async with factory() as session:
            team = await team_service(session).create_team(
                name="小队", intro="", description="", avatar_id=1, owner_id=captain
            )
            await TeamRepository(session).add_member(
                team.id, mate, TeamMemberRole.MEMBER
            )
            await session.commit()
        async with factory() as session:
            project = await ProjectService(session).create(
                name="P", owner_handle="captain", team_id=team.id
            )
            await session.commit()
            return str(project.id)

    pid = client.portal.call(seed)

    # 队友进来了；建项目的那个人也在，作为所有者，而不是靠小队那条路——他在小队里
    # 也有一行，但名册上他只出现一次。
    assert _roster(client, pid) == {"captain": "owner", "teammate": "team"}


def test_a_personal_project_pulls_nobody_in(client):
    """个人项目落在个人小队上，那里只有建项目的人自己——所以这条规则在那儿什么也
    不做，不会捎带任何别人进来。"""

    async def seed() -> str:
        factory = client.test_request_factory
        await _user(factory, "solo")
        async with factory() as session:
            await registered(session, "solo")
            project = await ProjectService(session).create(
                name="P", owner_handle="solo"
            )
            await session.commit()
            return str(project.id)

    pid = client.portal.call(seed)

    assert _roster(client, pid) == {"solo": "owner"}


def test_late_teammate_is_listed_without_a_persistent_project_grant(client):
    from app.domain.team.models import TeamMemberRole
    from app.domain.team.repositories import TeamRepository

    async def create() -> tuple[uuid.UUID, int, int]:
        factory = client.test_request_factory
        captain = await _user(factory, "late-captain")
        mate = await _user(factory, "late-mate")
        async with factory() as session:
            team = await team_service(session).create_team(
                name="小队", intro="", description="", avatar_id=1, owner_id=captain
            )
            project = await ProjectService(session).create(
                name="P", owner_handle="late-captain", team_id=team.id
            )
            await session.commit()
            return project.id, team.id, mate

    pid, team_id, mate = client.portal.call(create)
    assert _roster(client, str(pid)) == {"late-captain": "owner"}

    async def add() -> None:
        async with client.test_request_factory() as session:
            await TeamRepository(session).add_member(
                team_id, mate, TeamMemberRole.MEMBER
            )
            await session.commit()

    client.portal.call(add)
    assert len(_people(client, str(pid))) == 2
    late = _rows(client, str(pid))["late-mate"]
    assert late["source"] == "team"

    async def remove() -> None:
        async with client.test_request_factory() as session:
            assert (
                await MemberRepository(session).get(
                    project_id=pid, user_handle="late-mate"
                )
                is None
            )
            assert [
                m["handle"] for m in await ProjectRepository(session).people(pid)
            ] == ["late-captain", "late-mate"]
            repo = TeamRepository(session)
            relation = await repo.get_member_relation(team_id, mate)
            await repo.soft_delete_member(relation)
            await session.commit()

    client.portal.call(remove)
    # 退队的人从名册上消失；所有者不受影响。
    assert _roster(client, str(pid)) == {"late-captain": "owner"}
