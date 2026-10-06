"""「退出项目」退的是这个项目，不是小队。

在这条路修好之前，「谁在这个项目里」只有两条来路写得下来（所有者、外部成员）：队
友不写行，他在这份名册上是**读时**从 ``team_user_relation`` 继承的。于是「在小队
里」和「在这个项目里」是同一句话，他按下去只拿得到 409（去退小队）——退项目这颗按
钮其实只对外部成员成立。

``ProjectMemberExclusion`` 把第三件事记了下来：``(project_id, user_handle)`` 上有
这么一行 = 「他虽在小队里，但不属于这个项目」。这个文件钉的就是这条事实的每一面：

* 他退得掉，而且退干净了 —— 名册上没有他，``GET /projects/{id}``、``GET /topics?
  project_id=``、``GET /topics/{房间}`` 都拒绝他，房间里撤掉了他的席位（**包括项目
  总览那一间**）；
* 小队那边一个字节没变 —— ``team_user_relation`` 里他那行还在，该小队名下别的项目
  照常进得去，侧栏（``GET /projects``，不带 team_id）也照样列那一个；
* 显式加人就是把它清掉（邀请 → 接受；两条加人的路最后都落到 ``MemberRepository
  .add``），退出不是一次性的；
* 不动的两条边界：所有者仍然退不掉（理由指向转让），某间房最后一个 owner 仍然退不
  掉（理由点名那间房），而且后者**一个字节都不写**。

每个拒绝断言前面都先断言「之前是进得来的」—— 否则那几条 403 可能只是他本来就进不
去，什么也证明不了。
"""

import asyncio
import uuid
from datetime import UTC, datetime

from tests.conftest import seed_user


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- 场景：一支真小队，以及它名下的项目 ---------------------------------------


def team_of(client, *, owner: str, members: tuple[str, ...] = ()) -> int:
    """``owner`` 是 OWNER、``members`` 是 MEMBER 的一支**共享**小队；返回它的 id。

    小队成员按 int 用户 id 记（``team_user_relation``），和进项目那条来路完全一样：
    这里不另造一套身份，正是要证明退出项目没有碰它。
    """
    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    for handle in (owner, *members):
        seed_user(client, handle)
    holder: dict[str, int] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            team = Team(
                name=f"team of {owner}",
                handle=f"t-{owner}",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(team)
            await session.flush()
            users = UserRepository(session)
            for handles, role in (
                ((owner,), TeamMemberRole.OWNER),
                (members, TeamMemberRole.MEMBER),
            ):
                for handle in handles:
                    user = await users.get_by_username(handle)
                    assert user is not None
                    session.add(
                        TeamUserRelation(
                            team_id=team.id,
                            user_id=user.id,
                            role=role,
                            created_at=now,
                            updated_at=now,
                        )
                    )
            holder["id"] = team.id
            await session.commit()

    asyncio.run(_seed())
    return holder["id"]


def project_in(client, team_id: int, *, owner: str, name: str = "P") -> str:
    """小队里的一个项目，``owner`` 建的。返回项目 id。"""
    r = client.post(
        "/projects",
        json={"name": name, "team_id": team_id},
        headers=auth(seed_user(client, owner)),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def root_topic(client, pid: str, *, who: str) -> str:
    """项目总览那一间房 —— 建项目时种下的根话题。"""
    r = client.get(f"/projects/{pid}", headers=auth(seed_user(client, who)))
    assert r.status_code == 200, r.text
    root = r.json()["data"]["root_topic_id"]
    assert root, "每个项目建的时候都种了根话题"
    return root


def new_room(client, pid: str, *, created_by: str, title: str) -> str:
    r = client.post(
        "/topics",
        json={"project_id": pid, "title": title},
        headers=auth(seed_user(client, created_by)),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def seat(client, tid: str, handle: str, *, by: str) -> None:
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": handle},
        headers=auth(seed_user(client, by)),
    )
    assert r.status_code == 200, r.text


def roster(client, pid: str, *, who: str) -> dict[str, dict]:
    """名册，只有人（项目的芝士也在这份名册上，而这里问的是谁退出了）。"""
    r = client.get(f"/projects/{pid}/members", headers=auth(seed_user(client, who)))
    assert r.status_code == 200, r.text
    return {
        row["user_handle"]: row for row in r.json()["data"]["data"] if not row["agent"]
    }


def room_handles(client, tid: str, *, who: str) -> set[str]:
    """这间房的名册，只有人 —— 芝士的席位每间房都有一个，不是这里问的东西。"""
    r = client.get(f"/topics/{tid}/members", headers=auth(seed_user(client, who)))
    assert r.status_code == 200, r.text
    rows = r.json()["data"]["data"]
    return {m["member_handle"] for m in rows if not m["agent"]}


def reads(client, pid: str, tid: str, handle: str) -> bool:
    """他读得到这个项目、它的房间列表、以及那一间房吗？三处是三道不同的门。"""
    headers = auth(seed_user(client, handle))
    return (
        client.get(f"/projects/{pid}", headers=headers).status_code == 200
        and client.get(f"/topics?project_id={pid}", headers=headers).status_code == 200
        and client.get(f"/topics/{tid}", headers=headers).status_code == 200
    )


def status_of(client, path: str, handle: str) -> int:
    return client.get(path, headers=auth(seed_user(client, handle))).status_code


def leave(client, pid: str, handle: str):
    return client.delete(
        f"/projects/{pid}/membership", headers=auth(seed_user(client, handle))
    )


def my_projects(client, handle: str) -> set[str]:
    """侧栏读的那一份：``GET /projects`` 不带 team_id，答的是调用者自己的项目。"""
    r = client.get("/projects", headers=auth(seed_user(client, handle)))
    assert r.status_code == 200, r.text
    return {p["name"] for p in r.json()["data"]["data"]}


def invite(client, pid: str, *, by: str, who: str):
    return client.post(
        f"/projects/{pid}/invitations",
        json={"user_handle": who},
        headers=auth(seed_user(client, by)),
    )


def accept(client, invitation_id: str, who: str) -> None:
    r = client.post(
        f"/invitations/{invitation_id}/respond",
        json={"accept": True},
        headers=auth(seed_user(client, who)),
    )
    assert r.status_code == 200, r.text


def still_on_the_team(client, pid: str, handle: str) -> bool:
    """他还在这个项目所属的小队里吗 —— 问 ``team_user_relation`` 那一行，而不是
    任何读时合成的名册。"""
    from app.domain.project.models import Project
    from app.domain.team.repositories import TeamRepository
    from app.domain.user.repositories import UserRepository

    holder: dict[str, bool] = {}

    async def _ask() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            project = await session.get(Project, uuid.UUID(pid))
            assert project is not None
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None
            holder["on"] = await TeamRepository(session).is_team_member(
                project.team_id, user.id
            )

    asyncio.run(_ask())
    return holder["on"]


# ---- 1. 他退得掉，而且退干净了 -------------------------------------------------


def test_a_teammate_leaves_the_project_and_loses_it_room_by_room(client, bearer):
    """一个队友退出项目：名册上少一个人、三道门全关上、席位全撤，综合里也没有他了。

    「拒绝」的每一条前面都先断言他能进 —— 不然那几条 403 可能只是他本来就进不去。"""
    tid = team_of(client, owner="cap", members=("mate",))
    pid = project_in(client, tid, owner="cap")
    root = root_topic(client, pid, who="cap")
    room = new_room(client, pid, created_by="cap", title="设计讨论")
    # 综合不用给他座位：项目里的人都在综合里。
    assert "mate" in room_handles(client, root, who="cap")
    # 别的频道的座位是另发的。
    seat(client, room, "mate", by="cap")

    # 之前：他进得来，名册上按小队读出来，两间房都坐着他。
    assert reads(client, pid, root, "mate"), "退出之前他得是进得来的，否则证明不了什么"
    assert roster(client, pid, who="cap")["mate"]["source"] == "team"
    assert "mate" in room_handles(client, root, who="cap")
    assert "mate" in room_handles(client, room, who="cap")

    r = leave(client, pid, "mate")
    assert r.status_code == 200, r.text

    # 名册上真的少一个人 —— 别人也看得见，不是只对他自己隐藏。
    assert "mate" not in roster(client, pid, who="cap")
    assert "cap" in roster(client, pid, who="cap")
    # 三道门都拒绝他：项目、它的房间列表、以及那一间房（房间席位那张凭据）。
    assert status_of(client, f"/projects/{pid}", "mate") == 403
    assert status_of(client, f"/topics?project_id={pid}", "mate") == 403
    assert status_of(client, f"/topics/{root}", "mate") == 403
    assert status_of(client, f"/topics/{room}", "mate") == 403
    # 席位撤干净了；综合里也没有他了，因为他不在项目里了。
    assert "mate" not in room_handles(client, root, who="cap")
    assert "mate" not in room_handles(client, room, who="cap")
    # 别人原封不动。
    assert "cap" in room_handles(client, root, who="cap")


def test_leaving_a_project_does_not_touch_the_team(client, bearer):
    """小队里那点身份一个字节没变，该小队名下别的项目照常 —— 连侧栏也照常列它。

    这是同一条产品决定的另一半（退的是这个项目），也让「修法做过头」当场红掉：顺手
    把 ``team_user_relation`` 那一行也删掉、或者把队里所有项目一起关掉，都过不了这
    一条。"""
    tid = team_of(client, owner="cap", members=("mate",))
    pid = project_in(client, tid, owner="cap")
    other = project_in(client, tid, owner="cap", name="别的项目")
    root = root_topic(client, pid, who="cap")
    other_root = root_topic(client, other, who="cap")
    assert reads(client, pid, root, "mate")
    assert reads(client, other, other_root, "mate")
    assert my_projects(client, "mate") == {"P", "别的项目"}

    assert leave(client, pid, "mate").status_code == 200

    # 小队里还在，而且这一点是直接问那一行得到的，不是问名册。
    assert still_on_the_team(client, pid, "mate")
    assert still_on_the_team(client, other, "mate")
    # 小队的另一个项目照常进得去，侧栏照样列它。
    assert reads(client, other, other_root, "mate")
    assert "别的项目" in my_projects(client, "mate")
    # 退掉的那一个不再列 —— 列表和门答的是同一句话。
    assert "P" not in my_projects(client, "mate")


# ---- 2. 走了之后要能回来 -------------------------------------------------------


def test_an_invitation_takes_them_back(client, bearer):
    """显式把他加回来：邀请发得出去、接受得了，名册上重新出现，访问恢复。

    「他不在这个项目里」不是一堵单向墙。他不在名册上，所以「这个人已经在项目里了」
    为假，邀请发得出去；接受那一步落下的名册行把这条事实清掉。"""
    tid = team_of(client, owner="cap", members=("mate",))
    pid = project_in(client, tid, owner="cap")
    root = root_topic(client, pid, who="cap")
    assert reads(client, pid, root, "mate")
    assert leave(client, pid, "mate").status_code == 200
    assert "mate" not in roster(client, pid, who="cap")

    sent = invite(client, pid, by="cap", who="mate")
    assert sent.status_code == 200, sent.text
    accept(client, sent.json()["data"]["id"], "mate")

    # 名册上重新出现，门重新开（席位没补回来，项目成员身份本身就是那份凭据）。
    assert "mate" in roster(client, pid, who="cap")
    assert reads(client, pid, root, "mate")
    assert "P" in my_projects(client, "mate")
    assert still_on_the_team(client, pid, "mate")


def test_the_add_primitive_lifts_the_exclusion(client, bearer):
    """两条加人的路共用的那一个原语自己就是 Lift —— 这是「两条路都会清」的根据。

    邀请接受（``InvitationService.respond``）和给 AI 队友放座位（``MemberService
    .seat_agent``）最后都落到 ``MemberRepository.add``：清在**这一个**地方，就是清
    在两条路上，而不是各清一遍、漏一处。"""
    tid = team_of(client, owner="cap", members=("mate",))
    pid = project_in(client, tid, owner="cap")
    assert leave(client, pid, "mate").status_code == 200

    from app.domain.membership.repositories import MemberRepository

    holder: dict[str, bool] = {}

    async def _add_and_ask() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            repo = MemberRepository(session)
            assert await repo.is_excluded(
                project_id=uuid.UUID(pid), user_handle="mate"
            ), "退出项目记下的那条事实得先在"
            await repo.add(project_id=uuid.UUID(pid), user_handle="mate")
            holder["excluded"] = await repo.is_excluded(
                project_id=uuid.UUID(pid), user_handle="mate"
            )
            await session.commit()

    asyncio.run(_add_and_ask())
    assert holder["excluded"] is False
    assert "mate" in roster(client, pid, who="cap")


# ---- 3. 不动的两条边界 ---------------------------------------------------------


def test_the_owner_still_cannot_leave_the_project(client, bearer):
    """所有者仍然退不掉，理由仍然指向转让 —— 他一走这个项目就没人管得了。"""
    tid = team_of(client, owner="cap")
    pid = project_in(client, tid, owner="cap")
    root = root_topic(client, pid, who="cap")
    assert reads(client, pid, root, "cap")

    r = leave(client, pid, "cap")
    assert r.status_code == 403, r.text
    assert "转让" in r.json()["error"]["message"]
    assert "cap" in roster(client, pid, who="cap")
    assert reads(client, pid, root, "cap")


def test_a_channels_creator_leaves_and_its_people_stay(client, bearer):
    """一个频道的创建者退出项目：他走得掉，他的席位跟着撤掉，频道里别的人留着，
    他也读不到这个项目了。"""
    tid = team_of(client, owner="cap", members=("mate",))
    pid = project_in(client, tid, owner="cap")
    root = root_topic(client, pid, who="cap")
    mine = new_room(client, pid, created_by="mate", title="他建的频道")
    seat(client, mine, "cap", by="mate")
    assert room_handles(client, mine, who="cap") == {"mate", "cap"}

    r = leave(client, pid, "mate")
    assert r.status_code == 200, r.text
    assert "mate" not in roster(client, pid, who="cap")
    assert room_handles(client, mine, who="cap") == {"cap"}
    assert not reads(client, pid, root, "mate")


def test_someone_who_was_an_external_member_and_is_now_on_the_team_leaves_for_real(
    client, bearer
):
    """有成员行的队友退出之后不能从小队那边弹回来。

    他先是外部成员（一条成员行），后来进了小队：名册上他按 ``source == 'team'`` 显
    示（小队那条来路更靠前），成员行还在。退出时**只删那一行是不够的** —— 小队那条主
    张照旧把他放回名册。修法是同一趟把这条事实记下来。"""
    tid = team_of(client, owner="cap")
    pid = project_in(client, tid, owner="cap")
    root = root_topic(client, pid, who="cap")
    sent = invite(client, pid, by="cap", who="mate")
    accept(client, sent.json()["data"]["id"], "mate")
    assert roster(client, pid, who="cap")["mate"]["source"] == "external"
    # 之后才进小队 —— 两处身份现在都成立。
    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    async def _join() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username("mate")
            assert user is not None
            session.add(
                TeamUserRelation(
                    team_id=tid,
                    user_id=user.id,
                    role=TeamMemberRole.MEMBER,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_join())
    assert roster(client, pid, who="cap")["mate"]["source"] == "team"
    assert reads(client, pid, root, "mate")

    assert leave(client, pid, "mate").status_code == 200

    assert "mate" not in roster(client, pid, who="cap")
    assert not reads(client, pid, root, "mate")
    assert still_on_the_team(client, pid, "mate")


def test_a_project_handed_to_someone_who_left_it_still_has_an_owner(client, bearer):
    """接手一个自己退出过的项目的人，照样是所有者、照样进得来。

    「他不在这个项目里」说的是**小队带来的人不在这儿**，不是「这个 handle 永远进不
    来」。所有者是另一条来路（``projects.owner_handle``），它排在这条事实前面；一个
    退出过项目的人后来接手了它，名册上照样得有他 —— 否则这就成了一个没有所有者的项
    目。"""
    tid = team_of(client, owner="cap", members=("mate",))
    pid = project_in(client, tid, owner="cap")
    root = root_topic(client, pid, who="cap")
    assert reads(client, pid, root, "mate")
    assert leave(client, pid, "mate").status_code == 200
    assert "mate" not in roster(client, pid, who="cap")

    handed = client.put(
        f"/projects/{pid}/owner",
        json={"owner_handle": "mate"},
        headers=auth(seed_user(client, "cap")),
    )
    assert handed.status_code == 200, handed.text

    rows = roster(client, pid, who="cap")
    assert rows["mate"]["source"] == "owner"
    assert reads(client, pid, root, "mate")
    assert "P" in my_projects(client, "mate")
