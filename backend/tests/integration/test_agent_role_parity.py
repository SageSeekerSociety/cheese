"""Credentials identify participants; membership and roles grant permission."""

import pytest

from app.core.sandbox_auth import mint_scoped_token
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import (
    join_project_team,
    post_project,
    session_auth_headers,
)


def _rooms(client):
    project = post_project(
        client, json={"name": "Permissions", "owner_handle": "alice"}
    ).json()["data"]
    other = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Other", "created_by": "alice"},
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


def test_cross_room_access_requires_membership_and_preserves_identity(client):
    project, origin, other = _rooms(client)
    handle = _teammate(client, project, origin)
    auth = _agent(client, project, origin, as_handle=handle)
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 403
    _join(client, other, handle)
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 200
    written = client.post(
        f"/topics/{other}/comments",
        json={"content": "A participant in both rooms"},
        headers=auth,
    )
    assert written.status_code == 200, written.text
    assert written.json()["data"]["author"] == handle
    for action, body in (
        ("decision", {"decision": "Discussion in another joined room"}),
        ("ask", {"question": "Choose a day", "options": ["Monday", "Tuesday"]}),
    ):
        response = client.post(f"/topics/{other}/{action}", json=body, headers=auth)
        assert response.status_code == 200, response.text
        assert response.json()["data"]["author"] == handle
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
    import uuid

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
    endpoint = f"/topics/{origin}/decision"
    assert (
        client.post(
            endpoint, json={"decision": "Before removal"}, headers=auth
        ).status_code
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
        client.post(
            endpoint, json={"decision": "After removal"}, headers=auth
        ).status_code
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
        f"/topics/{other}/comments", json={"content": "Fixed identity"}, headers=auth
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
            f"/topics/{private}/decision", json={"decision": "Denied"}, headers=auth
        ).status_code
        == 403
    )
    _join(client, private, handle)
    assert client.get(f"/topics/{private}/blocks", headers=auth).status_code == 200


def test_room_management_uses_authenticated_role_not_a_claimed_actor(client):
    project, origin, _ = _rooms(client)
    auth = _agent(client, project, origin)
    handle = _seated_agent(client, origin)
    endpoint = f"/topics/{origin}/members"
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
    assert (
        client.put(
            f"{endpoint}/{handle}",
            json={"role": "admin"},
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
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


def test_turn_memory_remains_available_with_just_its_room_membership(client):
    project, origin, _ = _rooms(client)
    auth = _agent(client, project, origin)
    response = client.post(
        f"/projects/{project['id']}/memory",
        json={"topic": origin, "content": "A room-local observation"},
        headers=auth,
    )
    assert response.status_code == 200, response.text


def test_cloud_management_requires_team_standing_even_with_an_agent_credential(
    client,
):
    project, origin, _ = _rooms(client)
    auth = _agent(client, project, origin)
    endpoint = f"/projects/{project['id']}/machines"
    handle = _seated_agent(client, origin)
    join_project_team(client, project["id"], handle)
    assert client.post(endpoint, json={}, headers=auth).status_code == 403
    _promote(client, project["id"], handle)
    # No provider is configured in this harness. Reaching that check proves the
    # management grant passed without making an external provisioning request.
    assert client.post(endpoint, json={}, headers=auth).status_code == 422


def test_room_only_credential_cannot_use_project_management_roles(client):
    project, origin, _ = _rooms(client)
    join_project_team(client, project["id"], _seated_agent(client, origin), admin=True)
    auth = _agent(client, project, origin, scope="topic")
    for path, body in (
        ("members", {"user_handle": "bob"}),
        ("agent-credential", {}),
        ("machines", {}),
    ):
        assert (
            client.post(
                f"/projects/{project['id']}/{path}", json=body, headers=auth
            ).status_code
            == 403
        )
    assert (
        client.post(
            f"/projects/{project['id']}/memory",
            json={"topic": origin, "content": "Room memory"},
            headers=auth,
        ).status_code
        == 200
    )


def test_people_and_agents_can_ask_and_record_decisions_with_their_own_identity(client):
    project, origin, _ = _rooms(client)
    client.headers.pop("X-Cheese-Token", None)
    for handle, auth in (
        ("alice", session_auth_headers("alice")),
        (_seated_agent(client, origin), _agent(client, project, origin)),
    ):
        for action, body in (
            ("ask", {"question": "Which?", "options": ["A", "B"]}),
            ("decision", {"decision": "A shared decision"}),
        ):
            response = client.post(
                f"/topics/{origin}/{action}", json=body, headers=auth
            )
            assert response.status_code == 200, response.text
            assert response.json()["data"]["author"] == handle
            # 同一个动作，人做和分身做写下的是同一种事件：区别在署名那一行，
            # 不在档位。
            assert response.json()["data"]["author_type"] == "participant"


def test_review_actions_check_the_credentials_project_and_room(client):
    project, origin, _ = _rooms(client)
    foreign, _, room = _rooms(client)
    card = client.post(
        f"/topics/{room}/tasks/{delivery_task_id(client, room)}/accept-card",
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


def test_agent_reads_the_projects_record_through_the_room_it_works_in(client):
    """写决策的那条路一直通，读回来的一直没有 —— 读写要成对。

    ``cheese decision`` 走 ``POST /topics/{id}/decision``，周报同理。而读只有
    ``GET /projects/{id}/decisions`` 一条，它过去要求 ``authorize_project``：
    一轮里铸出来的凭据过不了那道门（见 ``_artifact_keeper``），于是同一个调用者
    写下决策、却一条也读不回来。``topic`` 就是产物清单和资料库早就接上的那个
    「点名自己的位置」参数，这里补上同一条。

    每条断言都是浏览器/CLI 会收到的状态码。
    """
    project, origin, _ = _rooms(client)
    auth = _agent(client, project, origin)
    pid = project["id"]
    written = {
        "decisions": ("decision", {"decision": "Ship on Friday"}),
        "weeklies": ("weekly", {"body": "Week 38 went out"}),
    }
    for collection, (action, body) in written.items():
        assert (
            client.post(
                f"/topics/{origin}/{action}", json=body, headers=auth
            ).status_code
            == 200
        )
        listed = client.get(
            f"/projects/{pid}/{collection}", params={"topic": origin}, headers=auth
        )
        assert listed.status_code == 200, listed.text
        assert [b["content"] for b in listed.json()["data"]["data"]] == [
            body.get("decision") or body["body"]
        ]


def test_naming_a_place_does_not_widen_what_an_agent_may_read(client):
    """不点名位置，仍然读不到；点到别人的项目、或点到自己没席位的房间，也读不到。

    这条是上面那条的边界：补 ``topic`` 只是把已有的一道门接上，不是放松它。
    """
    project, origin, other = _rooms(client)
    pid = project["id"]
    auth = _agent(client, project, origin)
    # 不点名位置：一轮的凭据本来就不是项目级凭据，照旧 403。
    assert client.get(f"/projects/{pid}/decisions", headers=auth).status_code == 403
    # 点一个不属于这个项目的房间：``authorized_place`` 挡掉。
    foreign, _, foreign_room = _rooms(client)
    assert (
        client.get(
            f"/projects/{pid}/decisions",
            params={"topic": foreign_room},
            headers=auth,
        ).status_code
        == 403
    )
    # 点一个自己没有席位的房间：席位即授权，所以这也不是一条进来的路。
    handle = _teammate(client, project, origin)
    only_here = _agent(client, project, origin, as_handle=handle)
    assert (
        client.get(
            f"/projects/{pid}/decisions", params={"topic": other}, headers=only_here
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/projects/{pid}/decisions", params={"topic": origin}, headers=only_here
        ).status_code
        == 200
    )
    assert foreign["id"] != pid


@pytest.mark.parametrize(
    "path",
    ["/projects/{pid}/tasks", "/projects/{pid}/milestones", "/topics?project_id={pid}"],
)
def test_agent_finds_the_projects_rooms_and_work_through_its_place(client, path):
    """同一个项目里别的房间、别的活、里程碑，芝士点名自己的位置就读得到。

    这是「不必让用户把项目里已有的东西逐条贴进来」的前提：房间、任务、里程碑这三份
    清单过去只认项目级凭据，一轮的凭据点了位置也是 403。不点位置、点别的项目的房间，
    仍然读不到。
    """
    project, origin, _ = _rooms(client)
    pid = project["id"]
    auth = _agent(client, project, origin)
    url = path.format(pid=pid)
    sep = "&" if "?" in url else "?"
    assert client.get(url, headers=auth).status_code == 403
    assert client.get(f"{url}{sep}topic={origin}", headers=auth).status_code == 200
    _, _, foreign_room = _rooms(client)
    assert (
        client.get(f"{url}{sep}topic={foreign_room}", headers=auth).status_code == 403
    )
