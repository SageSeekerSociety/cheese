"""Credentials identify participants; membership and roles grant permission."""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.ask_fixtures import active_ask
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    add_external_member,
    in_thread,
    join_project_team,
    post_project,
    session_auth_headers,
)


def _rooms(client):
    project = post_project(client, json={"name": "Permissions"}, owner="alice").json()[
        "data"
    ]
    other = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Other"},
        headers=session_auth_headers("alice"),
    ).json()["data"]["id"]
    return project, project["root_topic_id"], other


def _agent(client, project, origin, *, scope="project", ttl=3600, as_handle=None):
    """一位队友的每轮凭据，默认是 origin 里坐着的那一位。

    身份是**凭出来**的，不是从房间推出来的：一间房可以坐好几个 agent，所以铸令牌
    的那一刻必须说清是谁在用它。
    """
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"],
            topic_id=origin,
            access_scope=scope,
            ttl_s=ttl,
            agent_handle=as_handle or _seated_agent(client, origin),
        )
    }


def _teammate(client, project, room) -> str:
    """一个另外建出来的队友，只在 ``room`` 里有席位。

    项目自己那位芝士坐在项目的每一间房里（建房时就播进去），所以拿它问不出「这个
    房间认不认它」——两间房都认。跨房间的判据要用一个只坐了一间房的队友才问得出来。
    """
    made = client.post(
        f"/projects/{project['id']}/agents",
        json={"handle": "planner", "display_name": "规划师"},
    )
    assert made.status_code == 200, made.text
    seat = made.json()["data"]["seat_handle"]
    _join(client, room, seat)
    return seat


def _seated_agent(client, room: str) -> str:
    """房间名册上那条 agent 席位的 handle。

    「撤席位」「给席位升权」这类断言要问名册，不能自己按房间 id 拼一个名字出来：
    名字从 agent 自己来，项目总览坐的是项目芝士自己的席位。
    """
    body = client.get(f"/topics/{room}/members", headers=session_auth_headers("alice"))
    assert body.status_code == 200, body.text
    rows = body.json()["data"]["data"]
    return next(row["member_handle"] for row in rows if row["agent"])


def _join(client, room, handle):
    response = client.post(
        f"/topics/{room}/members",
        json={"handle": handle, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text


def test_cross_room_access_requires_membership_and_preserves_identity(
    client, stub_hooks, monkeypatch
):
    project, origin, other = _rooms(client)
    handle = _teammate(client, project, origin)
    auth = _agent(client, project, origin, as_handle=handle)
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 403
    question = {
        "questions": [
            {
                "question": "Choose a day",
                "options": [{"text": "Monday"}, {"text": "Tuesday"}],
            }
        ]
    }
    assert (
        client.post(f"/topics/{other}/asks", json=question, headers=auth).status_code
        == 403
    )
    _join(client, other, handle)
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 200
    response = client.post(
        f"/topics/{other}/weekly",
        json={"body": "Discussion in another joined room"},
        headers=auth,
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["author"] == handle
    # 芝士 answers — and asks — in a 支线 of the room.
    thread = in_thread(client, other, "alice")
    with active_ask(
        client, stub_hooks, monkeypatch, thread, actor="alice", seat=handle
    ):
        response = client.post(f"/topics/{thread}/asks", json=question, headers=auth)
        assert response.status_code == 200, response.text
        assert [row["author"] for row in response.json()["data"]["blocks"]] == [handle]
    assert (
        client.delete(
            f"/topics/{other}/members/{handle}",
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 403
    assert client.get(f"/topics/{origin}/blocks", headers=auth).status_code == 200


def test_room_only_credential_stays_restricted_even_with_membership(client):
    project, origin, other = _rooms(client)
    handle = _teammate(client, project, origin)
    _join(client, other, handle)
    assert (
        client.get(
            f"/topics/{other}/blocks",
            headers=_agent(client, project, origin, scope="topic", as_handle=handle),
        ).status_code
        == 403
    )


def test_project_access_cannot_cross_projects(client):
    project, origin, _ = _rooms(client)
    foreign, _, other = _rooms(client)
    handle = _teammate(client, project, origin)
    # 邻项目的房间连座位都给不了它：一个实例由建它的项目拥有，别处寻址不到它
    # （I9b）。所以这里问的是「就算凭据说得出它是谁，它也进不去别人的项目」。
    denied = client.post(
        f"/topics/{other}/members",
        json={"handle": handle, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert denied.status_code == 404, denied.text
    auth = _agent(client, project, origin, as_handle=handle)
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 403
    assert (
        client.get(f"/topics?project_id={foreign['id']}", headers=auth).status_code
        == 403
    )


def test_expired_or_forged_agent_credentials_never_become_anonymous(client):
    project, origin, _ = _rooms(client)
    for auth in (_agent(client, project, origin, ttl=-1), {"X-Cheese-Token": "forged"}):
        assert client.get(f"/topics/{origin}/blocks", headers=auth).status_code == 401


def test_removing_all_credentials_cannot_bypass_membership(client):
    project, origin, _ = _rooms(client)
    client.headers.pop("X-Cheese-Token", None)
    assert client.get(f"/topics/{origin}/blocks").status_code == 401
    assert client.get(f"/topics?project_id={project['id']}").status_code == 401


@pytest.mark.parametrize("is_agent", [False, True])
def test_team_standing_controls_management_for_both_identities(client, is_agent):
    """Managing the project is being an owner or admin of its team — the same
    rule for a person and for an agent's credential."""
    project, origin, _ = _rooms(client)
    handle = _seated_agent(client, origin) if is_agent else "bob"
    auth = _agent(client, project, origin) if is_agent else session_auth_headers(handle)
    invitations = f"/projects/{project['id']}/invitations"
    join_project_team(client, project["id"], handle)
    join_project_team(client, project["id"], "carol")
    assert (
        client.post(invitations, json={"user_handle": "dave"}, headers=auth).status_code
        == 403
    )
    _promote(client, project["id"], handle)
    assert (
        client.post(invitations, json={"user_handle": "dave"}, headers=auth).status_code
        == 200
    )
    assert (
        client.put(
            f"/projects/{project['id']}/owner",
            json={"owner_handle": "carol"},
            headers=auth,
        ).status_code
        == 200
    )


def _promote(client, pid: str, handle: str) -> None:
    """Make ``handle`` an admin of the project's team."""
    import asyncio

    from sqlalchemy import update

    from app.domain.project.models import Project
    from app.domain.team.models import TeamMemberRole, TeamUserRelation
    from app.domain.user.repositories import UserRepository

    async def _go() -> None:
        async with client.test_factory() as session:
            project = await session.get(Project, uuid.UUID(pid))
            user = await UserRepository(session).get_by_username(handle)
            await session.execute(
                update(TeamUserRelation)
                .where(
                    TeamUserRelation.team_id == project.team_id,
                    TeamUserRelation.user_id == user.id,
                    TeamUserRelation.deleted_at.is_(None),
                )
                .values(role=TeamMemberRole.ADMIN)
            )
            await session.commit()

    asyncio.run(_go())


def test_revoked_room_membership_also_closes_agent_write_gate(client):
    project, origin, _ = _rooms(client)
    auth = _agent(client, project, origin)
    endpoint = f"/topics/{origin}/weekly"
    assert (
        client.post(endpoint, json={"body": "Before removal"}, headers=auth).status_code
        == 200
    )
    handle = _seated_agent(client, origin)
    assert (
        client.delete(
            f"/topics/{origin}/members/{handle}",
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    assert (
        client.post(endpoint, json={"body": "After removal"}, headers=auth).status_code
        == 403
    )


def test_project_credential_has_one_identity_and_needs_a_grant(client):
    project, origin, other = _rooms(client)
    owner = session_auth_headers("alice")
    endpoint = f"/projects/{project['id']}/agent-credential"
    issued = client.post(endpoint, json={}, headers=owner)
    assert issued.status_code == 200, issued.text
    handle = _seated_agent(client, origin)
    assert issued.json()["data"]["agent_handle"] == handle
    auth = {"X-Cheese-Token": issued.json()["data"]["token"]}
    # 这位芝士本来就坐在项目的每一间房里，所以先把它从这一间撤下来——「席位即授权」
    # 这一问，只有在没有席位的房间里才问得出来。
    assert (
        client.delete(f"/topics/{other}/members/{handle}", headers=owner).status_code
        == 200
    )
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 403
    _join(client, other, handle)
    written = client.post(
        f"/topics/{other}/weekly", json={"body": "Fixed identity"}, headers=auth
    )
    assert written.status_code == 200, written.text
    assert written.json()["data"]["author"] == handle
    assert client.delete(endpoint, headers=owner).status_code == 200
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 401


@pytest.mark.parametrize("project_credential", [False, True])
def test_project_membership_never_opens_someone_elses_private_chat(
    client, project_credential
):
    project, origin, _ = _rooms(client)
    owner = session_auth_headers("alice")
    # 两个人之间的私聊：这个项目的芝士不是其中任何一方，所以「它是不是这间房的
    # 参与者」是真的在问，而不是在问一间它本来就坐在里面的房。
    private = client.get(
        f"/projects/{project['id']}/private-chat",
        params={"user_handle": "alice", "peer_handle": "bob"},
        headers=owner,
    ).json()["data"]["id"]
    handle = _seated_agent(client, origin)
    assert (
        client.post(
            f"/projects/{project['id']}/members",
            json={"user_handle": handle},
            headers=owner,
        ).status_code
        == 200
    )
    auth = _agent(client, project, origin)
    if project_credential:
        issued = client.post(
            f"/projects/{project['id']}/agent-credential", json={}, headers=owner
        )
        auth = {"X-Cheese-Token": issued.json()["data"]["token"]}
    assert (
        client.get(f"/topics?project_id={project['id']}", headers=auth).status_code
        == 200
    )
    assert client.get(f"/topics/{private}/blocks", headers=auth).status_code == 403
    assert (
        client.post(
            f"/topics/{private}/weekly", json={"body": "Denied"}, headers=auth
        ).status_code
        == 403
    )
    _join(client, private, handle)
    assert client.get(f"/topics/{private}/blocks", headers=auth).status_code == 200


def test_room_management_uses_authenticated_role_not_a_claimed_actor(client):
    project, origin, other = _rooms(client)
    auth = _agent(client, project, origin)
    handle = _seated_agent(client, origin)
    endpoint = f"/topics/{other}/members"
    join_project_team(client, project["id"], "bob")
    assert (
        client.post(endpoint, json={"handle": "bob", "actor": "alice"}).status_code
        == 401
    )
    assert (
        client.post(
            endpoint, json={"handle": "bob", "actor": "alice"}, headers=auth
        ).status_code
        == 403
    )
    # Once it manages the project — as an admin of its team, the way a person
    # would — it manages the channel too.
    join_project_team(client, project["id"], handle, admin=True)
    assert (
        client.post(endpoint, json={"handle": "bob"}, headers=auth).status_code == 200
    )


def test_manager_agent_can_issue_credentials_and_revocation_retires_them_all(client):
    project, origin, _ = _rooms(client)
    endpoint = f"/projects/{project['id']}/agent-credential"
    owner = session_auth_headers("alice")
    # The agent answers for the project the same way a person would: as an
    # admin of its team.
    join_project_team(client, project["id"], _seated_agent(client, origin), admin=True)
    issued = client.post(endpoint, json={}, headers=_agent(client, project, origin))
    auth = {"X-Cheese-Token": issued.json()["data"]["token"]}
    second = client.post(endpoint, json={}, headers=auth)
    assert second.status_code == 200, second.text
    assert client.delete(endpoint, headers=owner).status_code == 200
    for token in (issued.json()["data"]["token"], second.json()["data"]["token"]):
        assert (
            client.post(
                endpoint, json={}, headers={"X-Cheese-Token": token}
            ).status_code
            == 401
        )


def test_room_only_credential_cannot_use_project_management_roles(client):
    project, origin, _ = _rooms(client)
    join_project_team(client, project["id"], _seated_agent(client, origin), admin=True)
    auth = _agent(client, project, origin, scope="topic")
    # 这不是一张「房间级凭据什么都干不了」的清单，只有管理动作在这里：写记忆那
    # 一条曾经做对照（它 200），而写记忆的旧入口已经停用，对照没了就只剩正题。
    for path, body in (
        ("members", {"user_handle": "bob"}),
        ("agent-credential", {}),
    ):
        assert (
            client.post(
                f"/projects/{project['id']}/{path}", json=body, headers=auth
            ).status_code
            == 403
        )


def test_people_and_agents_record_weeklies_but_only_agents_create_questions(
    client, stub_hooks, monkeypatch
):
    project, origin, _ = _rooms(client)
    setup_token = client.headers.pop("X-Cheese-Token", None)
    for handle, auth in (
        ("alice", session_auth_headers("alice")),
        (_seated_agent(client, origin), _agent(client, project, origin)),
    ):
        response = client.post(
            f"/topics/{origin}/weekly", json={"body": "A shared weekly"}, headers=auth
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["author"] == handle
        assert response.json()["data"]["author_type"] == "participant"
        # A question is the agent's quick-reply message: a person's credential
        # cannot post one, and an agent's needs no turn running to.
        assert client.post(
            f"/topics/{origin}/asks",
            json={
                "questions": [
                    {
                        "question": "Which?",
                        "options": [{"text": "A"}, {"text": "B"}],
                    }
                ]
            },
            headers=auth,
        ).status_code == (403 if handle == "alice" else 200)
    seat = _seated_agent(client, origin)
    if setup_token is not None:
        client.headers["X-Cheese-Token"] = setup_token
    thread = in_thread(client, origin, "alice")
    with active_ask(client, stub_hooks, monkeypatch, thread, actor="alice", seat=seat):
        response = client.post(
            f"/topics/{thread}/asks",
            json={
                "questions": [
                    {"question": "Which?", "options": [{"text": "A"}, {"text": "B"}]}
                ]
            },
            headers=_agent(client, project, origin),
        )
        assert response.status_code == 200, response.text
        assert [row["author"] for row in response.json()["data"]["blocks"]] == [seat]
        assert response.json()["data"]["blocks"][0]["author_type"] == "participant"


def test_review_actions_check_the_credentials_project_and_room(client):
    project, origin, _ = _rooms(client)
    foreign, _, room = _rooms(client)
    card = client.post(
        f"/topics/{delivery_task_id(client, room)}/accept-card",
        headers=delivery_headers(client, room),
        json={
            "change_subject": "test: scoped review",
            "reviewer_handle": "alice",
            "routing_reason": "Review",
        },
    ).json()["data"]
    for action, body in (
        ("approve", {"approver_handle": "alice"}),
        ("accept", {"decided_by": "alice"}),
    ):
        assert (
            client.post(
                f"/accept-cards/{card['id']}/{action}",
                json=body,
                headers=_agent(client, project, origin),
            ).status_code
            == 403
        )


def test_agent_reads_back_the_weekly_it_wrote(client):
    """写周报的那条路一直通，读回来的也要通 —— 读写要成对。"""
    project, origin, _ = _rooms(client)
    auth = _agent(client, project, origin)
    body = {"body": "Week 38 went out"}
    assert (
        client.post(f"/topics/{origin}/weekly", json=body, headers=auth).status_code
        == 200
    )
    listed = client.get(f"/projects/{project['id']}/weeklies", headers=auth)
    assert listed.status_code == 200, listed.text
    assert [b["content"] for b in listed.json()["data"]["data"]] == [body["body"]]


@pytest.mark.parametrize(
    "path",
    [
        "/projects/{pid}",
        "/projects/{pid}/upstream",
        "/projects/{pid}/members",
        "/projects/{pid}/tasks",
        "/projects/{pid}/library",
        "/topics?project_id={pid}",
    ],
)
@pytest.mark.parametrize("where", ["room", "thread"])
def test_an_agent_reads_the_project_from_where_it_works(client, path, where):
    """芝士在项目的一个频道里干活，就读得到这个项目的东西：项目本身、关联的仓库、
    名册、别的频道和任务、资料库。它在频道的支线里也一样。不必在请求里再说一遍
    自己在哪 —— 凭据里写着。"""
    project, origin, _ = _rooms(client)
    place = origin if where == "room" else in_thread(client, origin, "alice")
    auth = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"],
            topic_id=place,
            access_scope="project",
            agent_handle=_seated_agent(client, origin),
        )
    }
    response = client.get(path.format(pid=project["id"]), headers=auth)
    assert response.status_code == 200, response.text


def test_an_agent_reads_a_project_only_from_a_room_it_sits_in(client):
    """读项目的资格来自它坐着的那个房间：凭据点的房间它不在，就读不到；
    别的项目的凭据也读不到这个项目。"""
    project, origin, other = _rooms(client)
    pid = project["id"]
    handle = _teammate(client, project, origin)
    from_other = _agent(client, project, other, as_handle=handle)
    from_origin = _agent(client, project, origin, as_handle=handle)
    upstream = f"/projects/{pid}/upstream"
    assert client.get(upstream, headers=from_other).status_code == 403
    assert client.get(upstream, headers=from_origin).status_code == 200
    foreign, foreign_room, _ = _rooms(client)
    elsewhere = _agent(client, foreign, foreign_room)
    assert client.get(upstream, headers=elsewhere).status_code == 403


def test_an_agent_does_not_read_the_project_into_a_room_an_outsider_reads(client):
    """芝士读到的东西会说进它所在的房间。房间里有项目以外的人时，它不能读项目的
    东西 —— 否则这个人借芝士就看到了项目不给他看的。

    项目外的人是这样进房间的：bob 作为外部成员和芝士开了私聊，后来退出了项目，
    私聊还在。"""
    project, origin, _ = _rooms(client)
    pid = project["id"]
    add_external_member(client, pid, "bob", by="alice")
    chat = client.get(
        f"/projects/{pid}/private-chat",
        params={"user_handle": "bob"},
        headers=session_auth_headers("bob"),
    )
    assert chat.status_code == 200, chat.text
    chat_id = chat.json()["data"]["id"]
    seat = next(
        row["member_handle"]
        for row in client.get(
            f"/topics/{chat_id}/members", headers=session_auth_headers("bob")
        ).json()["data"]["data"]
        if row["agent"]
    )
    in_chat = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=pid, topic_id=chat_id, access_scope="project", agent_handle=seat
        )
    }
    assert client.get(f"/projects/{pid}", headers=in_chat).status_code == 200
    left = client.delete(
        f"/projects/{pid}/membership", headers=session_auth_headers("bob")
    )
    assert left.status_code == 200, left.text
    assert client.get(f"/projects/{pid}", headers=in_chat).status_code == 403
    assert (
        client.get(
            f"/projects/{pid}/context/search",
            params={"q": "x", "topic": chat_id},
            headers=in_chat,
        ).status_code
        == 403
    )
    # A room with only the project's own people is unaffected.
    in_origin = _agent(client, project, origin)
    assert client.get(f"/projects/{pid}", headers=in_origin).status_code == 200


def test_reading_the_project_does_not_let_an_agent_change_it(client):
    """读得到不等于改得动：扔掉资料库里的一份，只有人能做。"""
    project, origin, _ = _rooms(client)
    response = client.delete(
        f"/projects/{project['id']}/library",
        params={"path": "预算表.xlsx"},
        headers=_agent(client, project, origin),
    )
    assert response.status_code == 403, response.text
