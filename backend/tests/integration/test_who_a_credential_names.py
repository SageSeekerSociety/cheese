"""Who a request is, whichever credential it carries, on the routes that ask
for a signed-in person: a team's own page, and someone's invitations.

Only a person's own sign-in acts as that person. An AI teammate's room
credential, a person's 芝士 on a route it was not given, the development
override and a token that does not verify are no one here, and change nothing.
"""

import uuid

import pytest

from app.core.sandbox_auth import (
    SANDBOX_TOKEN,
    mint_delegated_credential,
    mint_scoped_token,
)
from tests.conftest import seed_user
from tests.integration.conftest import post_project, room_agent_seat


@pytest.fixture
def team(client):
    """A team ``owner`` runs, and ``owner``'s sign-in."""
    handle = f"owner{uuid.uuid4().hex[:6]}"
    bearer = {"Authorization": f"Bearer {seed_user(client, handle)}"}
    made = client.post(
        "/teams",
        json={"name": "原名", "intro": "", "description": "", "avatarId": 1},
        headers=bearer,
    )
    assert made.status_code == 201, made.text
    made = made.json()["data"]["team"]
    return {
        "id": made["id"],
        "bearer": bearer,
        "user_id": made["owner"]["id"],
        "handle": handle,
    }


def _as(client, headers: dict[str, str]):
    """Requests carrying these headers and nothing the test client adds."""
    client.headers.pop("X-Cheese-Token", None)
    return headers


def _name(client, team) -> str:
    got = client.get(f"/teams/{team['id']}", headers=team["bearer"])
    return got.json()["data"]["team"]["name"]


def _rename(client, team, headers) -> int:
    return client.patch(
        f"/teams/{team['id']}", json={"name": "改了"}, headers=_as(client, headers)
    ).status_code


def test_a_persons_own_sign_in_acts_as_them(client, team):
    assert _rename(client, team, team["bearer"]) == 200
    assert _name(client, team) == "改了"


def test_an_ai_teammates_room_credential_is_no_person(client, team):
    project = post_project(client, json={"name": "P"}, headers=team["bearer"])
    project_id = project.json()["data"]["id"]
    room = client.post(
        "/topics",
        json={"project_id": project_id, "title": "房间"},
        headers=team["bearer"],
    ).json()["data"]["id"]
    teammate = {
        "X-Cheese-Token": mint_scoped_token(
            project_id=project_id,
            topic_id=room,
            agent_handle=room_agent_seat(client, room),
        )
    }

    assert _rename(client, team, teammate) >= 400
    assert (
        client.get("/users/me/team-invitations", headers=_as(client, teammate))
    ).status_code >= 400
    assert _name(client, team) == "原名"


def test_a_persons_assistant_reaches_only_the_routes_it_was_given(client, team):
    """芝士 acts for the person on the routes listed for it; a team's own
    settings are not among them, so it changes nothing there."""
    assistant = {
        "X-Cheese-Token": mint_delegated_credential(
            user_id=team["user_id"],
            handle=team["handle"],
            work=uuid.uuid4().hex,
            ttl_s=600,
            read_only=False,
        )
    }

    assert _rename(client, team, assistant) >= 400
    assert _name(client, team) == "原名"


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="nothing"),
        pytest.param({"Authorization": "Bearer not-a-token"}, id="garbage"),
        pytest.param({"X-Cheese-Token": SANDBOX_TOKEN}, id="development override"),
    ],
)
def test_without_a_persons_sign_in_it_is_no_one(client, team, headers):
    assert _rename(client, team, headers) == 401
    assert (
        client.get("/users/me/team-invitations", headers=_as(client, headers))
    ).status_code == 401
    assert _name(client, team) == "原名"
