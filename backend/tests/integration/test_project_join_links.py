"""A project's join link: permanent until reset, and joining through it either
lands on the roster or waits for a manager, as the project's approval says."""

import pytest

from tests.integration.conftest import room_agent_seat

OWNER = "owner-1"


@pytest.fixture
def project(client):
    return client.post(
        "/projects", json={"name": "Shared project", "owner_handle": OWNER}
    ).json()["data"]


@pytest.fixture
def link(client, bearer, project):
    response = client.get(f"/projects/{project['id']}/join-link", headers=bearer(OWNER))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def set_approval(client, bearer, project, approval):
    response = client.patch(
        f"/projects/{project['id']}/join-link",
        json={"approval": approval},
        headers=bearer(OWNER),
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def can_read_roster(client, bearer, project, person):
    path = f"/projects/{project['id']}/members"
    return client.get(path, headers=bearer(person)).status_code == 200


def pending_requests(client, bearer, project):
    response = client.get(
        f"/projects/{project['id']}/join-requests", headers=bearer(OWNER)
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_a_link_asks_for_approval_unless_turned_off(client, bearer, project, link):
    assert link["approval"] is True
    assert set_approval(client, bearer, project, False) == {
        "token": link["token"],
        "approval": False,
    }


def test_the_link_stays_the_same_until_reset(client, bearer, project, link):
    path = f"/projects/{project['id']}/join-link"
    again = client.get(path, headers=bearer(OWNER)).json()["data"]
    assert again["token"] == link["token"]
    assert "expires_at" not in again


def test_preview_is_not_consent_and_direct_join_grants_only_membership(
    client, bearer, project, link
):
    set_approval(client, bearer, project, False)
    shared = f"/project-invites/{link['token']}"
    response = client.get(shared, headers=bearer("visitor"))
    assert response.status_code == 200
    assert response.json()["data"] == {
        "project_id": project["id"],
        "project_name": "Shared project",
        "approval": False,
        "join_status": "none",
    }
    assert not can_read_roster(client, bearer, project, "visitor")
    for _ in range(2):
        response = client.post(
            shared + "/join",
            headers=bearer("visitor"),
            json={"role": "lead", "user_handle": "someone-else"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["data"]["join_status"] == "member"
    rows = client.get(
        f"/projects/{project['id']}/members", headers=bearer("visitor")
    ).json()["data"]["data"]
    assert [
        (r["user_handle"], r["role"]) for r in rows if r["user_handle"] == "visitor"
    ] == [("visitor", "member")]
    assert all(r["user_handle"] != "someone-else" for r in rows)


def test_with_approval_a_join_waits_until_a_manager_approves(
    client, bearer, project, link
):
    shared = f"/project-invites/{link['token']}"
    response = client.post(
        shared + "/join", headers=bearer("newcomer"), json={"message": "hi, I'm new"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["join_status"] == "pending"
    assert not can_read_roster(client, bearer, project, "newcomer")
    # Asking again does not file a second request.
    client.post(shared + "/join", headers=bearer("newcomer"))
    [request] = pending_requests(client, bearer, project)
    assert request["requester_handle"] == "newcomer"
    assert request["message"] == "hi, I'm new"

    approve = f"/projects/{project['id']}/join-requests/{request['id']}/approve"
    assert client.post(approve, headers=bearer(OWNER)).status_code == 200
    assert can_read_roster(client, bearer, project, "newcomer")
    assert (
        client.get(shared, headers=bearer("newcomer")).json()["data"]["join_status"]
        == "member"
    )
    assert pending_requests(client, bearer, project) == []
    assert client.post(approve, headers=bearer(OWNER)).status_code == 422


def test_a_rejected_person_stays_out_and_may_ask_again(client, bearer, project, link):
    shared = f"/project-invites/{link['token']}"
    client.post(shared + "/join", headers=bearer("newcomer"))
    [request] = pending_requests(client, bearer, project)
    reject = f"/projects/{project['id']}/join-requests/{request['id']}/reject"
    assert client.post(reject, headers=bearer(OWNER)).status_code == 200
    assert not can_read_roster(client, bearer, project, "newcomer")
    assert (
        client.get(shared, headers=bearer("newcomer")).json()["data"]["join_status"]
        == "none"
    )
    again = client.post(shared + "/join", headers=bearer("newcomer"))
    assert again.json()["data"]["join_status"] == "pending"


def test_the_link_can_be_shared_with_several_people(client, bearer, project, link):
    set_approval(client, bearer, project, False)
    for person in ("first", "second"):
        result = client.post(
            f"/project-invites/{link['token']}/join", headers=bearer(person)
        )
        assert result.status_code == 200, result.text
        assert can_read_roster(client, bearer, project, person)


def test_only_managers_handle_the_link_and_the_requests(client, bearer, project, link):
    set_approval(client, bearer, project, False)
    client.post(f"/project-invites/{link['token']}/join", headers=bearer("member"))
    set_approval(client, bearer, project, True)
    client.post(f"/project-invites/{link['token']}/join", headers=bearer("asker"))
    [request] = pending_requests(client, bearer, project)
    base = f"/projects/{project['id']}"
    for person in ("outsider", "member"):
        headers = bearer(person)
        assert client.get(base + "/join-link", headers=headers).status_code == 403
        assert (
            client.patch(
                base + "/join-link", json={"approval": False}, headers=headers
            ).status_code
            == 403
        )
        assert (
            client.post(base + "/join-link/reset", headers=headers).status_code == 403
        )
        assert client.get(base + "/join-requests", headers=headers).status_code == 403
        for decision in ("approve", "reject"):
            path = f"{base}/join-requests/{request['id']}/{decision}"
            assert client.post(path, headers=headers).status_code == 403


def test_reset_kills_the_old_link_but_keeps_members(client, bearer, project, link):
    set_approval(client, bearer, project, False)
    shared = f"/project-invites/{link['token']}"
    assert client.post(shared + "/join", headers=bearer("joined")).status_code == 200
    reset = client.post(
        f"/projects/{project['id']}/join-link/reset", headers=bearer(OWNER)
    )
    assert reset.status_code == 200
    renewed = reset.json()["data"]
    assert renewed["token"] != link["token"]
    assert renewed["approval"] is False
    assert client.get(shared, headers=bearer("new-person")).status_code == 404
    assert (
        client.post(shared + "/join", headers=bearer("new-person")).status_code == 404
    )
    assert can_read_roster(client, bearer, project, "joined")
    fresh = f"/project-invites/{renewed['token']}"
    assert client.get(fresh, headers=bearer("new-person")).status_code == 200


def test_an_owner_keeps_their_existing_role(client, bearer, project, link):
    shared = f"/project-invites/{link['token']}"
    assert (
        client.get(shared, headers=bearer(OWNER)).json()["data"]["join_status"]
        == "member"
    )
    assert client.post(shared + "/join", headers=bearer(OWNER)).status_code == 200
    rows = client.get(
        f"/projects/{project['id']}/members", headers=bearer(OWNER)
    ).json()["data"]["data"]
    assert next(r for r in rows if r["user_handle"] == OWNER)["role"] == "lead"
    assert pending_requests(client, bearer, project) == []


def test_agents_cannot_use_human_join_links(client, bearer, project, link):
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room", "created_by": OWNER},
    ).json()["data"]
    agent = room_agent_seat(client, topic["id"])
    result = client.post(
        f"/project-invites/{link['token']}/join", headers=bearer(agent)
    )
    assert result.status_code == 422


def test_link_requires_authentication(client, project, link):
    shared = f"/project-invites/{link['token']}"
    for method, path in (
        (client.get, shared),
        (client.post, shared + "/join"),
        (client.get, f"/projects/{project['id']}/join-link"),
        (client.post, f"/projects/{project['id']}/join-link/reset"),
    ):
        assert (
            method(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
        )


def alerts(client, bearer, project, person):
    response = client.get(f"/projects/{project['id']}/alerts", headers=bearer(person))
    assert response.status_code == 200, response.text
    return response.json()["data"]["data"]


def test_answering_the_managers_card_is_the_decision(client, bearer, project, link):
    added = client.post(
        f"/projects/{project['id']}/members",
        json={"user_handle": "lead-2", "role": "lead"},
        headers=bearer(OWNER),
    )
    assert added.status_code == 200, added.text
    client.post(f"/project-invites/{link['token']}/join", headers=bearer("newcomer"))
    cards = {
        person: next(
            a
            for a in alerts(client, bearer, project, person)
            if a["resolved_at"] is None
        )
        for person in (OWNER, "lead-2")
    }
    assert cards[OWNER]["title"] == "newcomer 申请加入项目「Shared project」"

    answered = client.post(
        f"/alerts/{cards['lead-2']['id']}/resolve",
        json={"chosen": "批准"},
        headers=bearer("lead-2"),
    )
    assert answered.status_code == 200, answered.text
    assert can_read_roster(client, bearer, project, "newcomer")
    for person in (OWNER, "lead-2"):
        [card] = [
            a
            for a in alerts(client, bearer, project, person)
            if a["id"] == cards[person]["id"]
        ]
        assert card["resolved_at"] is not None
    assert any(
        a["title"] == "你加入项目「Shared project」的申请已通过"
        for a in alerts(client, bearer, project, "newcomer")
    )


def test_answering_an_invitation_card_is_the_answer(client, bearer, project):
    invited = client.post(
        f"/projects/{project['id']}/invitations",
        json={"user_handle": "guest"},
        headers=bearer(OWNER),
    )
    assert invited.status_code == 200, invited.text
    [card] = alerts(client, bearer, project, "guest")
    answered = client.post(
        f"/alerts/{card['id']}/resolve",
        json={"chosen": "接受"},
        headers=bearer("guest"),
    )
    assert answered.status_code == 200, answered.text
    assert can_read_roster(client, bearer, project, "guest")
