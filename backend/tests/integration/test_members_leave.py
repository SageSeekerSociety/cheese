"""退出项目：成员自己走的那条路 (``DELETE /projects/{id}/membership``).

在那之前只有管理者能把人移出去，成员自己想走只能去求人。这条路的授权和写名
册那三条不同——动作的对象恒是动作人自己（身份只从 token 来，没有代退），挡在前面的
不是「你是不是管理员」，而是几种**走不了**的处境，每一种都得给出下一步：

* 所有者：他在名册上没有成员行，一走这个项目就没人管得了。

频道不拦人：管项目的人管它的每一个频道，谁走了都不会剩下一间没人管的房。

退的是**这个项目**，不是小队：一个访问来自小队的队友在这里不再被拒（从前是 409
「去退小队」），他按下这条路写下一条项目级事实（``ProjectMemberExclusion``），小队
那一行不动 —— ``test_leaving_a_project_is_not_leaving_the_team.py`` 是它那一份。

还有它必须做完的那半步：只删名册那一行的话，人还是每个房间都进得来（项目成员身份是
进得来全部话题的凭据，``authorize_topic_access`` 认它），所以席位要和成员行一起撤销。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.sandbox_auth import mint_scoped_token
from app.domain.project.services import ProjectService
from app.domain.team.models import TeamMemberRole
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import team_service
from app.domain.user.models import User
from tests.integration.conftest import (
    add_external_member,
    join_project_team,
    post_project,
    session_auth_headers,
)

OWNER = "owner-1"
MISSING_PROJECT = "00000000-0000-0000-0000-000000000000"


def _project(client, name: str = "P", owner: str = OWNER) -> str:
    return post_project(client, json={"name": name}, owner=owner).json()["data"]["id"]


def _add(client, pid: str, handle: str, *, by: str = OWNER) -> None:
    """``handle`` comes into the project from outside its team: invited, accepted."""
    add_external_member(client, pid, handle, by=by)


def _leave(client, pid: str, handle: str, **kw):
    return client.delete(
        f"/projects/{pid}/membership", headers=session_auth_headers(handle), **kw
    )


def _topic(client, pid: str, created_by: str, title: str | None = "房间") -> str:
    body = {"project_id": pid, "title": title}
    response = client.post(
        "/topics", json=body, headers=session_auth_headers(created_by)
    )
    return response.json()["data"]["id"]


def _managed_by(client, pid: str, handle: str, title: str) -> str:
    """A channel ``handle`` manages and the project's owner is not in. An
    external member does not open channels, so the owner opens it and hands
    it over."""
    tid = _topic(client, pid, OWNER, title=title)
    handed = client.put(
        f"/projects/{pid}/channels/{tid}/manager",
        json={"handle": handle},
        headers=session_auth_headers(OWNER),
    )
    assert handed.status_code == 200, handed.text
    left = client.post(f"/topics/{tid}/leave", headers=session_auth_headers(OWNER))
    assert left.status_code == 200, left.text
    return tid


def _seat(client, tid: str, handle: str, *, by: str) -> None:
    """把这个人放进一个频道 —— 由管这个频道的人放。"""
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": handle},
        headers=session_auth_headers(by),
    )
    assert r.status_code == 200, r.text


def _project_handles(client, pid: str) -> list[str]:
    """The people on the project roster. The roster also seats the project's own
    芝士 (one roster, agents included), and these tests are about who leaves."""
    rows = client.get(f"/projects/{pid}/members").json()["data"]["data"]
    return [m["user_handle"] for m in rows if not m.get("agent")]


def _topic_handles(client, tid: str) -> list[str]:
    rows = client.get(f"/topics/{tid}/members").json()["data"]["data"]
    return [m["member_handle"] for m in rows]


def _reads(client, pid: str, handle: str) -> bool:
    """这个人读得到这个项目吗 —— 名册那条路由就是那道门。"""
    r = client.get(f"/projects/{pid}/members", headers=session_auth_headers(handle))
    return r.status_code == 200


def _on_team(client, pid: str, handle: str) -> bool:
    """他还在这个项目所属的小队里吗 —— 直接问 ``team_user_relation`` 那一行。"""
    from app.domain.project.models import Project
    from app.domain.team.repositories import TeamRepository
    from app.domain.user.repositories import UserRepository

    holder: dict[str, bool] = {}

    async def _ask() -> None:
        async with client.test_request_factory() as session:
            project = await session.get(Project, uuid.UUID(pid))
            assert project is not None, pid
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            holder["on"] = await TeamRepository(session).is_team_member(
                project.team_id, user.id
            )

    client.portal.call(_ask)
    return holder["on"]


def test_a_member_leaves_and_their_topic_seats_go_with_them(client, bearer):
    """退项目 = 名册上没他 + 本项目各话题里也没他。

    两件事必须同时成立。席位留着的话，项目身份没了也照样每个房间都进得来 —— 退出
    就成了一件没做完的事。"""
    pid = _project(client)
    _add(client, pid, "alice")
    join_project_team(client, pid, "bob")
    tid = _topic(client, pid, "bob")
    _seat(client, tid, "alice", by="bob")
    assert "alice" in _topic_handles(client, tid)
    roster_before = set(_project_handles(client, pid))
    assert {OWNER, "alice"} <= roster_before

    r = _leave(client, pid, "alice")
    assert r.status_code == 200, r.text

    # The project agent and every other member must survive Alice's departure.
    assert set(_project_handles(client, pid)) == roster_before - {"alice"}
    assert "alice" not in _topic_handles(client, tid)
    # 别人不受影响：房间还在，室友和芝士的席位都还在。
    assert "bob" in _topic_handles(client, tid)


def test_removing_a_member_revokes_their_seats_too(client, bearer):
    """被移出项目的人也不该留着席位 —— 同一份撤销，两条路都走它。

    从前 ``remove`` 只删成员行，于是被移出的人从名册上消失了却还是每个房间都进得
    来。这条用例钉的是那个顺带修掉的缺口。"""
    pid = _project(client)
    _add(client, pid, "alice")
    join_project_team(client, pid, "bob")
    tid = _topic(client, pid, "bob")
    _seat(client, tid, "alice", by="bob")
    roster_before = set(_project_handles(client, pid))
    assert {OWNER, "alice"} <= roster_before

    r = client.delete(
        f"/projects/{pid}/members/alice", headers=session_auth_headers(OWNER)
    )
    assert r.status_code == 200, r.text

    assert set(_project_handles(client, pid)) == roster_before - {"alice"}
    assert "alice" not in _topic_handles(client, tid)


def test_removing_a_channels_creator_leaves_it_managed(client, bearer):
    """管理者把一个频道的创建者移出项目：她的席位跟着撤掉，房里的其他人留着，频道
    照样有人管 —— 管项目的人管它的每一个频道。"""
    pid = _project(client)
    _add(client, pid, "alice")
    _add(client, pid, "bob")
    _add(client, pid, "carol")
    tid = _managed_by(client, pid, "alice", "设计讨论")
    _seat(client, tid, "bob", by="alice")

    r = client.delete(
        f"/projects/{pid}/members/alice", headers=session_auth_headers(OWNER)
    )
    assert r.status_code == 200, r.text
    assert "alice" not in _project_handles(client, pid)
    assert "alice" not in _topic_handles(client, tid)
    assert "bob" in _topic_handles(client, tid)
    _seat(client, tid, "carol", by=OWNER)


def test_a_teammate_cannot_be_removed_from_the_project(client):
    """团队成员在名册上是从团队读出来的，没有一条可删的成员行 —— 移他是 404，人
    还在（这条路由仍然只对外部成员和 AI 队友的座位成立）。

    他自己走的路是 ``DELETE /projects/{id}/membership``，那是另一条路：它记的是
    「他在小队里，但不属于这个项目」，而这里缺的是一条能删的名册行。"""

    async def seed() -> str:
        factory = client.test_request_factory
        captain = await _user(factory, "captain")
        latecomer = await _user(factory, "latecomer")
        async with factory() as session:
            team = await team_service(session).create_team(
                name="小队", intro="", description="", avatar_id=1, owner_id=captain
            )
            project = await ProjectService(session).create(
                name="P", owner_handle="captain", team_id=team.id
            )
            await TeamRepository(session).add_member(
                team.id, latecomer, TeamMemberRole.MEMBER
            )
            await session.commit()
            return str(project.id)

    pid = client.portal.call(seed)

    rows = client.get(f"/projects/{pid}/members").json()["data"]["data"]
    row = next(m for m in rows if m["user_handle"] == "latecomer")
    assert row["source"] == "team"  # 读时补的，不是表里那一行

    r = client.delete(
        f"/projects/{pid}/members/latecomer", headers=session_auth_headers("captain")
    )
    assert r.status_code == 404, r.text
    assert "latecomer" in _project_handles(client, pid)


def test_the_owner_cannot_leave(client, bearer):
    """所有者退不了：他在名册上没有成员行（读时补出来的那一行），一走项目就没人
    管得了。得先转让所有权 —— 这句话要说给人听。"""
    pid = _project(client, owner="alice")

    r = _leave(client, pid, "alice")
    assert r.status_code == 403, r.text
    # 指向下一步：把项目转让给别人（界面上还没有这一颗按钮，所以文案不承诺它）。
    assert "转让" in r.json()["error"]["message"]
    assert "alice" in _project_handles(client, pid)


def test_a_non_member_gets_a_conflict(client, bearer):
    """不在名册上的人退不了 —— 和小队那条路同一种答复（``ConflictError``）。"""
    pid = _project(client)

    r = _leave(client, pid, "mallory")
    assert r.status_code == 409, r.text
    assert r.json()["error"]["message"] == "Not a member"


def test_a_channels_creator_leaves_and_the_others_stay(client, bearer):
    """频道的创建者退出项目：她走得掉，频道里的其他人留着，管项目的人照样管得了
    这个频道 —— 不会剩下一间没人管的房。"""
    pid = _project(client)
    _add(client, pid, "alice")
    join_project_team(client, pid, "bob")
    join_project_team(client, pid, "carol")
    tid = _managed_by(client, pid, "alice", "设计讨论")
    _seat(client, tid, "bob", by="alice")

    r = _leave(client, pid, "alice")
    assert r.status_code == 200, r.text
    assert "alice" not in _project_handles(client, pid)
    assert "alice" not in _topic_handles(client, tid)
    assert "bob" in _topic_handles(client, tid)
    _seat(client, tid, "carol", by=OWNER)


def test_a_room_with_only_its_owner_in_it_does_not_hold_them_back(client, bearer):
    """房里除了她只有芝士：没有谁会被留在一间没人管的房里，所以她退得掉。

    房间还在，项目里的人照样进得来，管项目的人管得了它。"""
    pid = _project(client)
    _add(client, pid, "alice")
    tid = _managed_by(client, pid, "alice", "她一个人的房")

    r = _leave(client, pid, "alice")

    assert r.status_code == 200, r.text
    assert "alice" not in _project_handles(client, pid)
    assert "alice" not in _topic_handles(client, tid)
    room = client.get(f"/topics/{tid}", headers=session_auth_headers(OWNER))
    assert room.status_code == 200, room.text


def test_a_previous_owner_leaves_the_project_and_stays_on_its_team(client, bearer):
    """把项目交给队友之后，原所有者在名册上按小队读出来 —— 他现在退得掉这个项目，
    而且退的是**这个项目**：小队那一行不动。

    从前这条路的答复是 409「去退团队」，因为「在小队里」就是「在这个项目里」。现在
    按下它写下 ``ProjectMemberExclusion``：名册上少他一个人，小队那边一个字节没变
    —— ``test_leaving_a_project_is_not_leaving_the_team.py`` 逐面钉的是它。"""
    pid = _project(client, owner="alice")
    join_project_team(client, pid, "bob")
    handed = client.put(
        f"/projects/{pid}/owner",
        json={"owner_handle": "bob"},
        headers=session_auth_headers("alice"),
    )
    assert handed.status_code == 200, handed.text

    rows = client.get(f"/projects/{pid}/members").json()["data"]["data"]
    sources = {m["user_handle"]: m.get("source") for m in rows if not m.get("agent")}
    assert sources["bob"] == "owner"
    assert sources["alice"] == "team"

    left = _leave(client, pid, "alice")
    assert left.status_code == 200, left.text
    assert "alice" not in _project_handles(client, pid)
    # 门也关了：名册那条路由拒绝她，而小队那一行还认得她。
    assert not _reads(client, pid, "alice")
    assert _on_team(client, pid, "alice")


def test_an_unverified_caller_cannot_leave(client, bearer):
    """没有凭证的请求退不了 —— 和写名册那三条路同一种拒绝（403）。

    身份只从 resolver 来，而匿名请求连「你是谁」都没说清；顺带钉住「不接受自称」：
    带着一个自称是 alice 的 body 也退不掉她。"""
    pid = _project(client)
    _add(client, pid, "alice")

    r = client.request(
        "DELETE",
        f"/projects/{pid}/membership",
        json={"actor": "alice", "user_handle": "alice"},
    )
    assert r.status_code == 403, r.text
    assert "alice" in _project_handles(client, pid)


def test_an_agent_token_cannot_leave_on_someone_elses_behalf(client, bearer):
    """芝士拿着这个项目的有效 token 也退不了别人：代退没有入口。

    它自己的身份（token 里那个 agent handle）不在这条名册上，于是拿到的是「不是成
    员」；alice 那一行原封不动。"""
    pid = _project(client)
    _add(client, pid, "alice")
    scoped = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=pid, agent_handle="unprivileged-agent"
        )
    }

    r = client.delete(f"/projects/{pid}/membership", headers=scoped)
    assert r.status_code == 409, r.text
    assert "alice" in _project_handles(client, pid)


def test_a_missing_project_is_404_not_403(client, bearer):
    """项目不存在读作 404：存在性检查在授权之前，和另外几条成员路由一致。"""
    r = client.delete(
        f"/projects/{MISSING_PROJECT}/membership",
        headers=session_auth_headers("alice"),
    )
    assert r.status_code == 404, r.text


async def _user(factory: Any, username: str) -> int:
    """一个真的知是用户，返回它的 int id。小队成员是按 int id 记的。"""
    async with factory() as session:
        now = datetime.now(UTC)
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


def test_a_teammate_leaves_the_project_without_leaving_the_team(client):
    """访问来自小队的人退得掉这个项目，退的是这个项目：小队那一行不动。

    从前的答复是 409，报错指向小队 —— 那时「在小队里」和「在这个项目里」是同一句
    话，他在这边点一次被拒一次，永远不知道该去哪儿。现在按下它写下
    ``ProjectMemberExclusion``，而小队那边一个字节没变，所以该小队的另一个项目他
    照常进得去。"""

    async def seed() -> tuple[str, str]:
        factory = client.test_request_factory
        captain = await _user(factory, "captain")
        mate = await _user(factory, "mate")
        async with factory() as session:
            team = await team_service(session).create_team(
                name="小队", intro="", description="", avatar_id=1, owner_id=captain
            )
            await TeamRepository(session).add_member(
                team.id, mate, TeamMemberRole.MEMBER
            )
            project = await ProjectService(session).create(
                name="P", owner_handle="captain", team_id=team.id
            )
            other = await ProjectService(session).create(
                name="Q", owner_handle="captain", team_id=team.id
            )
            await session.commit()
            return str(project.id), str(other.id)

    pid, other = client.portal.call(seed)
    assert _reads(client, pid, "mate")
    assert _reads(client, other, "mate")

    r = _leave(client, pid, "mate")
    assert r.status_code == 200, r.text
    assert "mate" not in _project_handles(client, pid)
    assert not _reads(client, pid, "mate")
    assert _reads(client, other, "mate"), "小队那一行不该被动过"
