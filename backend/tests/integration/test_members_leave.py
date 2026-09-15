"""「我退出这个项目」—— 名册上唯一一条不需要 owner/lead 的写。

test_members_authz.py 问的是「谁能改别人的名册」；这一条问的反方向：成员能不能
把自己摘下来，以及三种摘不掉的情形 —— 他是所有者、他压根不在名册上、他只是靠
所属小队进得来（对这个项目没有成员行）。

判断「退没退成」一律看项目的门：``GET /topics?project_id=`` 是工作区打开项目时
第一件要做的事，它 200/403 就是「他还在不在里面」。
"""

import asyncio
from datetime import UTC, datetime

from tests.conftest import seed_user

OWNER = "alice"


def _project(client, owner: str = OWNER) -> str:
    r = client.post("/projects", json={"name": "P", "owner_handle": owner})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _add(client, pid: str, handle: str, role: str = "member", **kw):
    return client.post(
        f"/projects/{pid}/members", json={"user_handle": handle, "role": role}, **kw
    )


def _handles(client, pid: str) -> list[str]:
    """被**写进**名册的那些人（所有者、小队带进来的人不是任何一次写的结果，跳过，
    理由同 test_members_authz.py）。"""
    body = client.get(f"/projects/{pid}/members").json()
    return [m["user_handle"] for m in body["data"]["data"] if "source" not in m]


def _enters(client, pid: str, headers: dict[str, str]) -> int:
    return client.get(
        "/topics", params={"project_id": pid}, headers=headers
    ).status_code


def _leave(client, pid: str, headers: dict[str, str]):
    return client.delete(f"/projects/{pid}/members/me", headers=headers)


def test_member_leaves_and_the_door_closes(client, bearer):
    """这条路径必须真的落到 leave 上，而不是被 /members/{user_handle} 抢走：抢走
    的话普通成员会撞上 owner/lead 那道闸，拿到 403 而不是 200。"""
    pid = _project(client)
    _add(client, pid, "bob", headers=bearer(OWNER))
    assert _enters(client, pid, bearer("bob")) == 200

    r = _leave(client, pid, bearer("bob"))
    assert r.status_code == 200
    assert r.json()["data"]["left"] is True

    assert _handles(client, pid) == []
    # 退出不是只在界面上生效的姿态：名册删了，项目的门也就关了。
    assert _enters(client, pid, bearer("bob")) == 403


def test_a_lead_can_leave_too(client, bearer):
    """组长退的是自己，不是别人，所以那道 manager 闸对他同样不该落下。"""
    pid = _project(client)
    _add(client, pid, "bob", role="lead", headers=bearer(OWNER))
    assert _leave(client, pid, bearer("bob")).status_code == 200
    assert _handles(client, pid) == []


def test_the_owner_cannot_leave(client, bearer):
    """所有者不在成员表里（名单上那一行是读的时候拼出来的），删他要删的那一行
    什么也改不了——所以这里说清楚要交所有权，而不是返回一个骗人的成功。"""
    pid = _project(client)
    r = _leave(client, pid, bearer(OWNER))
    assert r.status_code == 403
    assert "所有权" in r.json()["message"]
    assert _enters(client, pid, bearer(OWNER)) == 200


def test_a_non_member_cannot_leave_anything(client, bearer):
    """路径里写死 me，所以这里不存在「删别人」的写法：mallory 带上自己的 token
    也只能问到自己，而答案是没有。"""
    pid = _project(client)
    _add(client, pid, "bob", headers=bearer(OWNER))

    r = _leave(client, pid, bearer("mallory"))
    assert r.status_code == 404
    assert _handles(client, pid) == ["bob"]


def test_an_anonymous_caller_cannot_leave(client, bearer):
    """没有凭据的调用者 resolve 成 anonymous，而退出是一次写，得知道是谁在写。

    答案是 401（先登录）而不是 403：这里缺的是身份，不是权限 —— 和
    ``authorize_project`` 对同一批调用者说的话一致。注意上面那几条管理接口对匿名
    调用者给的是 403（它们先撞上 owner/lead 那道闸），两处不是一回事。
    """
    pid = _project(client)
    _add(client, pid, "bob", headers=bearer(OWNER))

    assert client.delete(f"/projects/{pid}/members/me").status_code == 401
    assert _handles(client, pid) == ["bob"]


def test_a_teammate_cannot_leave_through_this_route(client):
    """靠小队进项目的人对这个项目没有成员行 —— 他能看见项目、能进话题，但这里
    没有一行可以删，出路是退出小队。界面上这些人带 source，不给这颗按钮；这条
    测的是真被调到时的答案（404，而不是假装成功或者删掉别人）。

    注意 bob 必须是**建项目之后**才进队的：建项目时队里的人会被
    ``ProjectService._seed_roster`` 落成真正的成员行（他也就真的能退出），
    只有后进来的人才是纯粹靠小队继承的那一类。
    """
    team_id = _team(client, owner=OWNER, members=())
    pid = _project_with_team(client, owner=OWNER, team_id=team_id)
    bob = {"Authorization": f"Bearer {seed_user(client, 'bob')}"}
    assert _enters(client, pid, bob) == 403

    _join_team(client, team_id, "bob")
    assert _enters(client, pid, bob) == 200
    assert "bob" not in _handles(client, pid)

    assert _leave(client, pid, bob).status_code == 404
    # 退不掉，但也没被踢出去：他还在，只是这条路对他什么也不做。
    assert _enters(client, pid, bob) == 200


# ---- 把一个项目挂到一个小队上 ------------------------------------------------


def _team(client, *, owner: str, members: tuple[str, ...]) -> int:
    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    for handle in (owner, *members):
        seed_user(client, handle)

    holder: dict[str, int] = {}

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            team = Team(
                name=f"team-of-{owner}",
                intro="",
                description="",
                avatar_id=1,
                created_at=now,
                updated_at=now,
            )
            session.add(team)
            await session.flush()
            users = UserRepository(session)
            for handle, role in (
                (owner, TeamMemberRole.OWNER),
                *((m, TeamMemberRole.MEMBER) for m in members),
            ):
                user = await users.get_by_username(handle)
                assert user is not None, handle
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


def _join_team(client, team_id: int, handle: str) -> None:
    """建完项目之后才进队 —— 这正是「对这个项目没有成员行」的那一类人。"""
    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    seed_user(client, handle)

    async def _seed() -> None:
        now = datetime.now(UTC)
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None, handle
            session.add(
                TeamUserRelation(
                    team_id=team_id,
                    user_id=user.id,
                    role=TeamMemberRole.MEMBER,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_seed())


def _project_with_team(client, *, owner: str, team_id: int) -> str:
    r = client.post(
        "/projects",
        json={"name": "P", "owner_handle": owner, "team_id": team_id},
        headers={"Authorization": f"Bearer {seed_user(client, owner)}"},
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]
