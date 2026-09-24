"""退出项目：成员自己走的那条路 (``DELETE /projects/{id}/membership``).

在那之前只有管理者能把人移出去，成员自己想走只能去求人。这条路的授权和写名
册那三条不同——动作的对象恒是动作人自己（身份只从 token 来，没有代退），挡在前面的
不是「你是不是管理员」，而是几种**走不了**的处境，每一种都得给出下一步：

* 所有者：他在名册上没有成员行，一走这个项目就没人管得了；
* 团队成员：他的访问来自团队，退得出的是团队；
* 某个话题最后一个 owner：撤掉他，那间房就没有人管得了（无主房间是死路）。

还有它必须做完的那半步：只删名册那一行的话，人还是每个房间都进得来（项目成员身份是
进得来全部话题的凭据，``authorize_topic_access`` 认它），所以席位要和成员行一起撤销。
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.core.sandbox_auth import mint_scoped_token
from app.domain.project.services import ProjectService
from app.domain.team.models import TeamMemberRole
from app.domain.team.repositories import TeamRepository
from app.domain.team.services import team_service
from app.domain.topic.models import TopicMembership, TopicRole
from app.domain.topic_membership.repositories import TopicMembershipRepository
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
    body = {"name": name, "owner_handle": owner}
    return post_project(client, json=body).json()["data"]["id"]


def _add(client, pid: str, handle: str, *, by: str = OWNER) -> None:
    """``handle`` comes into the project from outside its team: invited, accepted."""
    add_external_member(client, pid, handle, by=by)


def _leave(client, pid: str, handle: str, **kw):
    return client.delete(
        f"/projects/{pid}/membership", headers=session_auth_headers(handle), **kw
    )


def _topic(client, pid: str, created_by: str, title: str = "房间") -> str:
    body = {"project_id": pid, "title": title, "created_by": created_by}
    return client.post("/topics", json=body).json()["data"]["id"]


def _seat(client, tid: str, handle: str, *, by: str, role: str = "member") -> None:
    """把一个话题席位给这个人 —— 由这间房的 owner 给。"""
    r = client.post(
        f"/topics/{tid}/members",
        json={"handle": handle, "role": role},
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


def test_a_member_leaves_and_their_topic_seats_go_with_them(client, bearer):
    """退项目 = 名册上没他 + 本项目各话题里也没他。

    两件事必须同时成立。席位留着的话，项目身份没了也照样每个房间都进得来 —— 退出
    就成了一件没做完的事。"""
    pid = _project(client)
    _add(client, pid, "alice")
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


def test_removing_the_last_owner_of_a_topic_writes_nothing(client, bearer):
    """管理者也移不掉「某间房唯一的 owner」，而且这条路上不许留半成品。

    同一个撤销（``revoke_project_seats``）两条路共用，所以「最后一个 owner」这条规矩
    在移出这边一样管用。这条用例另钉一件事：拒绝时**两个表都原封不动**。撤销和删成
    员行是两次写，次序错了就会出现「席位没了，人还留在名册上」这种半成品 —— 比什么
    都不做更难收拾，因为名册上那个人已经进不去任何房间了。"""
    pid = _project(client)
    _add(client, pid, "alice")
    tid = _topic(client, pid, "alice", title="设计讨论")

    r = client.delete(
        f"/projects/{pid}/members/alice", headers=session_auth_headers(OWNER)
    )
    assert r.status_code == 422, r.text
    assert "设计讨论" in r.json()["message"]
    assert "alice" in _project_handles(client, pid)
    assert "alice" in _topic_handles(client, tid)


def test_a_teammate_cannot_be_removed_from_the_project(client):
    """团队成员在名册上是从团队读出来的，没有一条可删的成员行 —— 移他是 404，人
    还在。他离开项目的路是离开团队。"""

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


def test_the_owner_query_locks_the_rows_it_reads(client):
    """``owners_by_topic`` 真的把这些 owner 行锁住了 —— 「两个人同时退」的根据。

    「最后一个 owner」是**读出来再决定**的，两笔并发退项目的各自读到「这间房有两个
    owner」，就会各自把自己删掉，房间照样没人管。修法是在读的时候落行锁，让第二个事
    务看见第一个提交后的结果。

    这里不去写「两个协程一起退」那种用例：它要靠调度顺序才撞得上，跑一百次里红一次
    也说明不了什么。改成直接问数据库：一个事务拿住这些行不放，另一个连接
    ``FOR UPDATE NOWAIT`` 去要同一批行，立刻被拒 —— NOWAIT 不等待，所以时序是确定的。
    """
    pid = _project(client)
    _add(client, pid, "alice")
    tid = _topic(client, pid, "alice")
    topic_id = uuid.UUID(tid)

    async def contend() -> None:
        factory = client.test_request_factory
        async with factory() as holder:
            locked = await TopicMembershipRepository(holder).owners_by_topic([topic_id])
            assert locked == {topic_id: ["alice"]}
            async with factory() as other:
                # asyncpg's LockNotAvailableError is wrapped as DBAPIError.
                with pytest.raises(DBAPIError) as err:
                    await other.execute(
                        select(TopicMembership)
                        .where(
                            TopicMembership.topic_id == topic_id,
                            TopicMembership.role == TopicRole.owner,
                        )
                        .with_for_update(nowait=True)
                    )
                assert "lock" in str(err.value).lower()

    client.portal.call(contend)


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


def test_the_last_owner_of_a_topic_cannot_walk_out(client, bearer):
    """他是某间房唯一的 owner 时退不了 —— 撤掉他，那间房就没 owner 了。

    这不是「少了一个人」：只有 owner / admin 能管名册，而无主房间在产品里是死路
    （``TopicMemberService._require_manager`` 那个逃逸口就是为修这种房间存在的）。
    所以报错要点名是哪间房，并且**什么也不改**。"""
    pid = _project(client)
    _add(client, pid, "alice")
    tid = _topic(client, pid, "alice", title="设计讨论")

    r = _leave(client, pid, "alice")
    assert r.status_code == 422, r.text
    # ``ValidationError`` 属于 cheesex 那一族（``app/core/errors.py``），响应是
    # ``{"code", "message", "data"}``，没有 BaseError 那层的 ``error``。
    assert "设计讨论" in r.json()["message"]
    # 一个字节都没动：成员行和席位都在。
    assert "alice" in _project_handles(client, pid)
    assert "alice" in _topic_handles(client, tid)

    # 而且这不是死结：把房间交给别人（一位队友）之后，她就走得掉了。
    join_project_team(client, pid, "bob")
    _seat(client, tid, "bob", by="alice")
    handed_over = client.put(
        f"/topics/{tid}/members/bob",
        json={"role": "owner"},
        headers=session_auth_headers("alice"),
    )
    assert handed_over.status_code == 200, handed_over.text
    assert _leave(client, pid, "alice").status_code == 200


def test_a_previous_owner_is_still_in_the_project_through_its_team(client, bearer):
    """把项目交给队友之后，原所有者仍在这个项目的团队里，所以仍在项目里 —— 他要
    离开得退团队，退项目这条路会指向团队。"""
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

    refused = _leave(client, pid, "alice")
    assert refused.status_code == 409, refused.text
    assert "团队" in refused.json()["error"]["message"]


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


def test_a_teammate_is_told_to_leave_the_team(client):
    """访问来自小队的人退不了项目：他退得出的是小队。

    报错必须指向小队 —— 否则他在项目这边点一次、被拒一次，永远不知道该去哪儿。
    席位也不该被这位退场顺手撤掉：他的访问本来就不是项目发的（``list_members``
    读时继承，不留第二份授权），退的是小队。"""

    async def seed() -> str:
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
            await session.commit()
            return str(project.id)

    pid = client.portal.call(seed)

    assert "mate" in _project_handles(client, pid)

    r = _leave(client, pid, "mate")
    assert r.status_code == 409, r.text
    assert "团队" in r.json()["error"]["message"]
    assert "mate" in _project_handles(client, pid)
