"""邀请：加人这件事要两个人同意。

为什么不是一步到位——进了项目就看得见这个项目的**全部话题**，那是别人的工作内容，
不该由邀请方单方面决定谁能看。所以这一份钉的全是「谁做的决定」：没答复之前不算成
员、只有本人能答复、答复过的邀请不能再答一次，以及那条待办不会在答复之后还挂在别
人的收件箱里等一个已经没有答案的问题。

另外两条钉的是**谁可以被邀请**：执行身份（带 agent_bindings 的 user）不进人名册，
以及「已经在项目里」要按完整名册算而不是成员表。
"""

import asyncio
import uuid

from app.domain.identity.handles import topic_agent_handle

OWNER = "owner-1"


def _project(client, name: str = "Demo") -> str:
    r = client.post("/projects", json={"name": name, "owner_handle": OWNER})
    assert r.status_code == 200
    return r.json()["data"]["id"]


def _invite(client, bearer, project_id: str, handle: str, role: str = "member"):
    return client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": handle, "role": role},
        headers=bearer(OWNER),
    )


def _handles(client, project_id: str) -> set[str]:
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    return {m["user_handle"] for m in rows}


def test_an_invitation_does_not_put_anyone_on_the_roster(client, bearer):
    project_id = _project(client)
    r = _invite(client, bearer, project_id, "alice", "lead")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "pending"

    # 名册上没有她——这就是这个功能的全部意义。
    assert "alice" not in _handles(client, project_id)
    pending = client.get(f"/projects/{project_id}/invitations").json()["data"]["data"]
    assert [(i["invitee_handle"], i["role"]) for i in pending] == [("alice", "lead")]


def test_accepting_is_what_joins_the_project(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice", "lead").json()["data"]

    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "accepted"
    assert "alice" in _handles(client, project_id)
    # 邀请上写的角色就是她进来时的角色。
    rows = client.get(f"/projects/{project_id}/members").json()["data"]["data"]
    assert next(m for m in rows if m["user_handle"] == "alice")["role"] == "lead"
    # 答复完就不再挂在待答复里。
    assert (
        client.get(f"/projects/{project_id}/invitations").json()["data"]["data"] == []
    )


def test_declining_leaves_the_roster_alone(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": False},
        headers=bearer("alice"),
    )
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "declined"
    assert "alice" not in _handles(client, project_id)


def test_only_the_invitee_can_answer(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    # 连发邀请的人自己都不行——否则「要对方同意」就是一句空话。
    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer(OWNER),
    )
    assert r.status_code == 403
    assert "alice" not in _handles(client, project_id)

    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("mallory"),
    )
    assert r.status_code == 403


def test_an_answered_invitation_cannot_be_answered_again(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]
    client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": False},
        headers=bearer("alice"),
    )
    # 界面上那颗按钮点两下不该得到一句莫名其妙的 404。
    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    assert r.status_code == 422
    assert "答复过" in r.json()["message"]
    assert "alice" not in _handles(client, project_id)


def test_declining_leaves_the_door_open_for_a_second_invitation(client, bearer):
    """拒绝一次不等于永远进不来——唯一约束只管「同时只能有一张待答复的」。"""
    project_id = _project(client)
    first = _invite(client, bearer, project_id, "alice").json()["data"]
    client.post(
        f"/invitations/{first['id']}/respond",
        json={"accept": False},
        headers=bearer("alice"),
    )
    assert _invite(client, bearer, project_id, "alice").status_code == 200


def test_the_same_person_is_not_invited_twice_over(client, bearer):
    project_id = _project(client)
    assert _invite(client, bearer, project_id, "alice").status_code == 200
    r = _invite(client, bearer, project_id, "alice")
    assert r.status_code == 422
    assert "等他答复" in r.json()["message"]


def test_someone_already_in_the_project_is_not_invited(client, bearer):
    project_id = _project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "alice"},
        headers=bearer(OWNER),
    )
    r = _invite(client, bearer, project_id, "alice")
    assert r.status_code == 422
    assert "已经在项目里" in r.json()["message"]


def test_only_a_manager_may_invite(client, bearer):
    project_id = _project(client)
    client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "bob", "role": "member"},
        headers=bearer(OWNER),
    )
    r = client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": "alice"},
        headers=bearer("bob"),
    )
    assert r.status_code == 403


def test_an_invitation_says_which_project_it_is_for(client, bearer):
    """被邀请的人还不在这个项目里，任何项目作用域的接口他都够不着——项目名不由后端
    带出来，界面上就只能写「有人邀请你加入一个项目」，那是一句没法据以决定的话。"""
    project_id = _project(client, "推荐算法原型")
    _invite(client, bearer, project_id, "alice")
    mine = client.get("/me/invitations", headers=bearer("alice")).json()["data"]["data"]
    assert [i["project_name"] for i in mine] == ["推荐算法原型"]


def test_my_invitations_are_addressed_to_me_only(client, bearer):
    a = _project(client, "A")
    b = _project(client, "B")
    _invite(client, bearer, a, "alice")
    _invite(client, bearer, b, "bob")

    mine = client.get("/me/invitations", headers=bearer("alice")).json()["data"]["data"]
    assert [(i["project_id"], i["invitee_handle"]) for i in mine] == [(a, "alice")]
    # 被邀请的人还不在那个项目里，所以这条接口不能是项目作用域的——否则他根本够不着。
    assert (
        client.get("/me/invitations", headers=bearer("carol")).json()["data"]["data"]
        == []
    )


def test_an_answered_invitation_stops_waiting_in_the_inbox(client, bearer):
    """邀请是一条**待办**：答复之前不消失，答复之后必须消失。

    不结掉的话，一张已经答复的邀请会永远挂在收件箱里等他回答一个已经没有答案的
    问题。
    """
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    inbox = client.get(
        f"/projects/{project_id}/inbox?target_handle=alice", headers=bearer("alice")
    ).json()["data"]["data"]
    waiting = [a for a in inbox if a["resolved_at"] is None]
    assert waiting, "收到邀请应该在收件箱里留一条待办"
    assert any("邀请你加入项目" in a["title"] for a in waiting)

    client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    inbox = client.get(
        f"/projects/{project_id}/inbox?target_handle=alice", headers=bearer("alice")
    ).json()["data"]["data"]
    assert [a for a in inbox if a["resolved_at"] is None] == []


def test_revoking_takes_the_invitation_back(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]

    r = client.delete(f"/invitations/{invitation['id']}", headers=bearer(OWNER))
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "revoked"
    assert (
        client.get(f"/projects/{project_id}/invitations").json()["data"]["data"] == []
    )
    # 撤回之后对方点接受也没用。
    r = client.post(
        f"/invitations/{invitation['id']}/respond",
        json={"accept": True},
        headers=bearer("alice"),
    )
    assert r.status_code == 422
    assert "alice" not in _handles(client, project_id)


def test_an_outsider_cannot_revoke(client, bearer):
    project_id = _project(client)
    invitation = _invite(client, bearer, project_id, "alice").json()["data"]
    r = client.delete(f"/invitations/{invitation['id']}", headers=bearer("mallory"))
    assert r.status_code == 403


# --- 谁可以被邀请 ---------------------------------------------------------------
# 一眼看不出区别的两类拒绝，都是真发生过的：成员页按 uid 查人，查得到 agent（它
# 就是一行 user），也查得到早就通过小队在项目里的人（他不在成员表里）。判断都得
# 落在「这个人是谁、他在不在项目里」，而不是「成员表里有没有这一行」。


def _seed_agent(client, handle: str) -> None:
    """造一个 agent-user：真实的 User 行 + platform binding，和线上一模一样。"""
    from app.domain.identity.services import IdentityService

    async def _run() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            await IdentityService(session).ensure_agent_user(handle=handle)
            await session.commit()

    asyncio.run(_run())


def test_an_agent_is_not_invited_into_a_project(client, bearer):
    """成员页那个框收 uid，而 agent 的 uid 和真人同一段序列——查得到，但名册不收。

    agent 进项目走的是 Agent 配置那条路（项目页的「AI 队友」），不是人名册。
    """
    project_id = _project(client)
    _seed_agent(client, "cheese-elsewhere")

    r = _invite(client, bearer, project_id, "cheese-elsewhere")
    assert r.status_code == 422
    assert "AI 队友" in r.json()["message"]
    assert "cheese-elsewhere" not in _handles(client, project_id)


def test_a_topic_derived_agent_handle_is_not_invited(client, bearer):
    """真的去拿一个话题分身的 handle——它就是最容易被照着填进来的那个。

    handle 是 ``cheese-<话题hex>``，判据不能是「长得像不像」，只能是不是带
    agent_bindings 的 user：格式是派生的，不是契约。
    """
    project_id = _project(client)
    topic_id = client.post(
        "/topics",
        json={"project_id": project_id, "title": "T", "created_by": OWNER},
    ).json()["data"]["id"]
    handle = topic_agent_handle(uuid.UUID(topic_id))

    r = _invite(client, bearer, project_id, handle)
    assert r.status_code == 422
    assert "AI 队友" in r.json()["message"]
    assert handle not in _handles(client, project_id)


def test_an_agent_is_not_added_to_the_roster_directly_either(client, bearer):
    """邀请那条堵住了不算数——直接写名册那条是同一个洞的另一半。"""
    project_id = _project(client)
    _seed_agent(client, "cheese-direct")

    r = client.post(
        f"/projects/{project_id}/members",
        json={"user_handle": "cheese-direct"},
        headers=bearer(OWNER),
    )
    assert r.status_code == 422
    assert "AI 队友" in r.json()["message"]
    assert "cheese-direct" not in _handles(client, project_id)


def test_a_teammate_who_joined_after_the_project_is_not_invited(client, bearer):
    """「已经在项目里」按完整名册算，不是查成员表。

    小队成员不在 ``project_members`` 里，读名册的时候才补出来。只查表的话他会再被
    邀请一次，而接受之后落下的那一行会在**退队之后继续生效**——小队这条授权本来是
    按读时推导、不留副本的。
    """
    team_id = _make_team(client, "teamlead")
    r = client.post(
        "/projects",
        json={"name": "P", "owner_handle": OWNER, "team_id": team_id},
    )
    assert r.status_code == 200, r.text
    project_id = r.json()["data"]["id"]

    # 建项目之后才进队的人——建项目那次铺名册碰不到他。
    _add_to_team(client, team_id, "bob")
    assert "bob" in _handles(client, project_id), "他在名册上（读的时候补出来的）"

    r = _invite(client, bearer, project_id, "bob")
    assert r.status_code == 422
    assert "已经在项目里" in r.json()["message"]
    assert (
        client.get(f"/projects/{project_id}/invitations").json()["data"]["data"] == []
    )


def _make_team(client, owner: str) -> int:
    """``owner`` 一个人的小队。成员按 user id 存，所以先要有真实的 User 行。"""
    from tests.conftest import seed_user

    seed_user(client, owner)
    return _insert_team(client, owner)


def _add_to_team(client, team_id: int, handle: str) -> None:
    from tests.conftest import seed_user

    seed_user(client, handle)
    _insert_team_relation(client, team_id, handle)


def _insert_team(client, owner: str) -> int:
    from datetime import UTC, datetime

    from app.domain.team.models import Team, TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    holder: dict[str, int] = {}

    async def _run() -> None:
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
            user = await UserRepository(session).get_by_username(owner)
            assert user is not None, owner
            session.add(
                TeamUserRelation(
                    team_id=team.id,
                    user_id=user.id,
                    role=TeamMemberRole.OWNER,
                    created_at=now,
                    updated_at=now,
                )
            )
            holder["id"] = team.id
            await session.commit()

    asyncio.run(_run())
    return holder["id"]


def _insert_team_relation(client, team_id: int, handle: str) -> None:
    from datetime import UTC, datetime

    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    async def _run() -> None:
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

    asyncio.run(_run())
