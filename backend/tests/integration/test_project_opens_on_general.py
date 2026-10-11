"""A project says whether its AI teammate has spoken in 综合 yet: until it has,
the page opens the project on 综合, where the starter cards are."""

import uuid

from tests.integration.conftest import (
    new_project,
    post_message,
    room_agent_headers,
    session_auth_headers,
)


def _listed(client, project_id: str) -> dict:
    rows = client.get("/projects", headers=session_auth_headers("owner")).json()
    return next(p for p in rows["data"]["data"] if p["id"] == project_id)


def test_only_the_teammate_speaking_in_general_ends_the_start(client):
    project = new_project(client, "Notes")
    assert project["agent_spoke_in_general"] is False
    general = project["root_topic_id"]

    # The person writing first does not end it: the cards are still what they need.
    post_message(client, general, "owner", {"content": "hello"})
    assert _listed(client, project["id"])["agent_spoke_in_general"] is False

    said = client.post(
        f"/topics/{general}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "Hi, what shall we do?"},
        headers=room_agent_headers(client, general),
    )
    assert said.status_code == 200, said.text
    assert _listed(client, project["id"])["agent_spoke_in_general"] is True


def test_each_project_answers_for_its_own_general(client):
    quiet = new_project(client, "Quiet")
    busy = new_project(client, "Busy")
    client.post(
        f"/topics/{busy['root_topic_id']}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "Ready."},
        headers=room_agent_headers(client, busy["root_topic_id"]),
    )
    assert _listed(client, quiet["id"])["agent_spoke_in_general"] is False
    assert _listed(client, busy["id"])["agent_spoke_in_general"] is True
