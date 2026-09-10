"""Credentials identify participants; membership and roles grant permission."""

import uuid

import pytest

from app.core.sandbox_auth import mint_scoped_token
from app.domain.identity.handles import topic_agent_handle
from tests.delivery import delivery_headers, delivery_task_id
from tests.integration.conftest import session_auth_headers


def _rooms(client):
    project = client.post(
        "/projects", json={"name": "Permissions", "owner_handle": "alice"}
    ).json()["data"]
    other = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Other", "created_by": "alice"},
    ).json()["data"]["id"]
    return project, project["root_topic_id"], other


def _agent(project, origin, *, scope="project", ttl=3600):
    return {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project["id"],
            topic_id=origin,
            access_scope=scope,
            ttl_s=ttl,
        )
    }


def _join(client, room, handle):
    response = client.post(
        f"/topics/{room}/members",
        json={"handle": handle, "role": "member"},
        headers=session_auth_headers("alice"),
    )
    assert response.status_code == 200, response.text


def test_cross_room_access_requires_membership_and_preserves_identity(client):
    project, origin, other = _rooms(client)
    handle = topic_agent_handle(uuid.UUID(origin))
    auth = _agent(project, origin)
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
    _join(client, other, topic_agent_handle(uuid.UUID(origin)))
    assert (
        client.get(
            f"/topics/{other}/blocks",
            headers=_agent(project, origin, scope="topic"),
        ).status_code
        == 403
    )


def test_project_access_cannot_cross_projects(client):
    project, origin, _ = _rooms(client)
    foreign, _, other = _rooms(client)
    _join(client, other, topic_agent_handle(uuid.UUID(origin)))
    auth = _agent(project, origin)
    assert client.get(f"/topics/{other}/blocks", headers=auth).status_code == 403
    assert (
        client.get(f"/topics?project_id={foreign['id']}", headers=auth).status_code
        == 403
    )


def test_expired_or_forged_agent_credentials_never_become_anonymous(client):
    project, origin, _ = _rooms(client)
    for auth in (_agent(project, origin, ttl=-1), {"X-Cheese-Token": "forged"}):
        assert client.get(f"/topics/{origin}/blocks", headers=auth).status_code == 401


def test_removing_all_credentials_cannot_bypass_membership(client):
    project, origin, _ = _rooms(client)
    client.headers.pop("X-Cheese-Token", None)
    assert client.get(f"/topics/{origin}/blocks").status_code == 401
    assert client.get(f"/topics?project_id={project['id']}").status_code == 401


@pytest.mark.parametrize("is_agent", [False, True])
def test_project_role_controls_management_for_both_identities(client, is_agent):
    project, origin, _ = _rooms(client)
    handle = topic_agent_handle(uuid.UUID(origin)) if is_agent else "bob"
    auth = _agent(project, origin) if is_agent else session_auth_headers(handle)
    roster = f"/projects/{project['id']}/members"
    owner = session_auth_headers("alice")
    added = client.post(roster, json={"user_handle": handle}, headers=owner)
    assert added.status_code == 200, added.text
    assert (
        client.post(roster, json={"user_handle": "carol"}, headers=auth).status_code
        == 403
    )
    assert (
        client.put(
            f"{roster}/{handle}", json={"role": "lead"}, headers=auth
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"{roster}/{handle}", json={"role": "lead"}, headers=owner
        ).status_code
        == 200
    )
    assert (
        client.post(roster, json={"user_handle": "carol"}, headers=auth).status_code
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
    assert (
        client.put(
            f"{roster}/{handle}", json={"role": "member"}, headers=auth
        ).status_code
        == 200
    )
    assert client.delete(f"{roster}/carol", headers=auth).status_code == 403


def test_revoked_room_membership_also_closes_agent_write_gate(client):
    project, origin, _ = _rooms(client)
    auth = _agent(project, origin)
    endpoint = f"/topics/{origin}/decision"
    assert (
        client.post(
            endpoint, json={"decision": "Before removal"}, headers=auth
        ).status_code
        == 200
    )
    handle = topic_agent_handle(uuid.UUID(origin))
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
    handle = topic_agent_handle(uuid.UUID(origin))
    assert issued.json()["data"]["agent_handle"] == handle
    auth = {"X-Cheese-Token": issued.json()["data"]["token"]}
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
    private = client.get(
        f"/projects/{project['id']}/private-chat",
        params={"user_handle": "alice"},
        headers=owner,
    ).json()["data"]["id"]
    handle = topic_agent_handle(uuid.UUID(origin))
    assert (
        client.post(
            f"/projects/{project['id']}/members",
            json={"user_handle": handle},
            headers=owner,
        ).status_code
        == 200
    )
    auth = _agent(project, origin)
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
    auth = _agent(project, origin)
    handle = topic_agent_handle(uuid.UUID(origin))
    endpoint = f"/topics/{origin}/members"
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
    assert (
        client.post(
            f"/projects/{project['id']}/members",
            json={"user_handle": topic_agent_handle(uuid.UUID(origin)), "role": "lead"},
            headers=owner,
        ).status_code
        == 200
    )
    issued = client.post(endpoint, json={}, headers=_agent(project, origin))
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
    auth = _agent(project, origin)
    response = client.post(
        f"/projects/{project['id']}/memory",
        json={"topic": origin, "content": "A room-local observation"},
        headers=auth,
    )
    assert response.status_code == 200, response.text


def test_cloud_management_requires_role_even_with_an_agent_credential(client):
    project, origin, _ = _rooms(client)
    auth = _agent(project, origin)
    endpoint = f"/projects/{project['id']}/machines"
    owner = session_auth_headers("alice")
    handle = topic_agent_handle(uuid.UUID(origin))
    roster = f"/projects/{project['id']}/members"
    assert (
        client.post(roster, json={"user_handle": handle}, headers=owner).status_code
        == 200
    )
    assert client.post(endpoint, json={}, headers=auth).status_code == 403
    assert (
        client.put(
            f"{roster}/{handle}", json={"role": "lead"}, headers=owner
        ).status_code
        == 200
    )
    # No provider is configured in this harness. Reaching that check proves the
    # management grant passed without making an external provisioning request.
    assert client.post(endpoint, json={}, headers=auth).status_code == 422


def test_room_only_credential_cannot_use_project_management_roles(client):
    project, origin, _ = _rooms(client)
    assert (
        client.post(
            f"/projects/{project['id']}/members",
            json={"user_handle": topic_agent_handle(uuid.UUID(origin)), "role": "lead"},
            headers=session_auth_headers("alice"),
        ).status_code
        == 200
    )
    auth = _agent(project, origin, scope="topic")
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
        (topic_agent_handle(uuid.UUID(origin)), _agent(project, origin)),
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
            assert response.json()["data"]["author_type"] == (
                "human" if handle == "alice" else "ai"
            )


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
                headers=_agent(project, origin),
            ).status_code
            == 403
        )
