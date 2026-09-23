"""A shared link needs a manager's grant and the recipient's confirmation."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.domain.project.models import ProjectJoinLink
from tests.integration.conftest import room_agent_seat

OWNER = "owner-1"


@pytest.fixture
def invitation_link(client, bearer):
    project = client.post(
        "/projects", json={"name": "Shared project", "owner_handle": OWNER}
    ).json()["data"]
    path = f"/projects/{project['id']}/join-link"
    response = client.post(path, headers=bearer(OWNER))
    assert response.status_code == 200, response.text
    return project, response.json()["data"]


def test_preview_is_not_consent_and_join_grants_only_membership(
    client, bearer, invitation_link
):
    project, link = invitation_link
    path = f"/project-invites/{link['token']}"
    outsider = bearer("visitor")
    assert (
        client.get(f"/projects/{project['id']}/members", headers=outsider).status_code
        == 403
    )
    response = client.get(path, headers=outsider)
    assert response.status_code == 200
    assert response.json()["data"]["project_name"] == "Shared project"
    assert response.json()["data"]["already_member"] is False
    assert (
        client.get(f"/projects/{project['id']}/members", headers=outsider).status_code
        == 403
    )
    for _ in range(2):
        response = client.post(
            path + "/join",
            headers=outsider,
            json={"role": "lead", "user_handle": "someone-else"},
        )
        assert response.status_code == 200, response.text
    rows = client.get(f"/projects/{project['id']}/members", headers=outsider).json()[
        "data"
    ]["data"]
    assert [
        (r["user_handle"], r["role"]) for r in rows if r["user_handle"] == "visitor"
    ] == [("visitor", "member")]
    assert all(r["user_handle"] != "someone-else" for r in rows)
    assert client.get(path, headers=outsider).json()["data"]["already_member"] is True


def test_link_can_be_shared_with_multiple_people(client, bearer, invitation_link):
    project, link = invitation_link
    for person in ("first", "second"):
        result = client.post(
            f"/project-invites/{link['token']}/join", headers=bearer(person)
        )
        assert result.status_code == 200, result.text
        assert (
            client.get(
                f"/projects/{project['id']}/members", headers=bearer(person)
            ).status_code
            == 200
        )


def test_only_managers_can_read_create_or_revoke_links(client, bearer, invitation_link):
    project, link = invitation_link
    path = f"/projects/{project['id']}/join-link"
    client.post(f"/project-invites/{link['token']}/join", headers=bearer("member"))
    for person in ("outsider", "member"):
        for method in (client.get, client.post, client.delete):
            assert method(path, headers=bearer(person)).status_code == 403
    assert (
        client.get(path, headers=bearer(OWNER)).json()["data"]["token"] == link["token"]
    )
    assert (
        client.post(path, headers=bearer(OWNER)).json()["data"]["token"]
        == link["token"]
    )


def test_revocation_blocks_old_links_but_keeps_existing_members(
    client, bearer, invitation_link
):
    project, link = invitation_link
    path = f"/projects/{project['id']}/join-link"
    shared = f"/project-invites/{link['token']}"
    assert client.post(shared + "/join", headers=bearer("joined")).status_code == 200
    assert client.delete(path, headers=bearer(OWNER)).status_code == 200
    assert client.get(path, headers=bearer(OWNER)).json()["data"] is None
    assert client.get(shared, headers=bearer("new-person")).status_code == 404
    assert (
        client.post(shared + "/join", headers=bearer("new-person")).status_code == 404
    )
    assert (
        client.get(
            f"/projects/{project['id']}/members", headers=bearer("joined")
        ).status_code
        == 200
    )
    renewed = client.post(path, headers=bearer(OWNER)).json()["data"]
    assert renewed["token"] != link["token"]


def test_expired_links_cannot_be_used_and_can_be_replaced(
    client, bearer, invitation_link
):
    project, link = invitation_link

    async def expire():
        async with client.test_factory() as session:
            row = await session.scalar(
                select(ProjectJoinLink).where(ProjectJoinLink.token == link["token"])
            )
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire())
    shared = f"/project-invites/{link['token']}"
    assert client.get(shared, headers=bearer("visitor")).status_code == 404
    assert client.post(shared + "/join", headers=bearer("visitor")).status_code == 404
    path = f"/projects/{project['id']}/join-link"
    assert client.get(path, headers=bearer(OWNER)).json()["data"] is None
    renewed = client.post(path, headers=bearer(OWNER)).json()["data"]
    assert renewed["token"] != link["token"]


def test_an_owner_keeps_their_existing_role(client, bearer, invitation_link):
    project, link = invitation_link
    shared = f"/project-invites/{link['token']}"
    assert (
        client.get(shared, headers=bearer(OWNER)).json()["data"]["already_member"]
        is True
    )
    assert client.post(shared + "/join", headers=bearer(OWNER)).status_code == 200
    rows = client.get(f"/projects/{project['id']}/members").json()["data"]["data"]
    assert next(r for r in rows if r["user_handle"] == OWNER)["role"] == "lead"


def test_agents_cannot_use_human_join_links(client, bearer, invitation_link):
    project, link = invitation_link
    topic = client.post(
        "/topics",
        json={"project_id": project["id"], "title": "Room", "created_by": OWNER},
    ).json()["data"]
    agent = room_agent_seat(client, topic["id"])
    result = client.post(
        f"/project-invites/{link['token']}/join", headers=bearer(agent)
    )
    assert result.status_code == 422


def test_link_requires_authentication(client, invitation_link):
    project, link = invitation_link
    shared = f"/project-invites/{link['token']}"
    for method, path in (
        (client.get, shared),
        (client.post, shared + "/join"),
        (client.post, f"/projects/{project['id']}/join-link"),
    ):
        assert (
            method(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
        )
