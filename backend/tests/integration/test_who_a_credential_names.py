"""Who a request is, whichever credential it carries, on the routes that ask
for a signed-in person: a team's own page, and someone's invitations.

Only a person's own sign-in, or their 芝士 acting for them, acts as that
person; their 芝士 does not reach their own hold on it. An AI teammate's room
credential, the development override and a token that does not verify are no
one here, and change nothing.
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
    client.headers.pop("Authorization", None)
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
        "Authorization": f"Bearer {
            mint_scoped_token(
                project_id=project_id,
                topic_id=room,
                agent_handle=room_agent_seat(client, room),
            )
        }"
    }

    assert _rename(client, team, teammate) >= 400
    assert (
        client.get("/users/me/team-invitations", headers=_as(client, teammate))
    ).status_code >= 400
    assert _name(client, team) == "原名"


def test_a_persons_cheese_acts_as_them_but_does_not_change_what_it_asks_first(
    client, team
):
    """What 芝士 does for the person is judged as theirs; what it asks them
    first is theirs to set, not something 芝士 resets for them."""
    assistant = {
        "Authorization": f"Bearer {
            mint_delegated_credential(
                user_id=team['user_id'],
                handle=team['handle'],
                work=uuid.uuid4().hex,
                ttl_s=600,
                read_only=False,
            )
        }"
    }
    rules = {"ask": [], "askItems": [], "allowItems": ["什么都直接做"]}

    assert _rename(client, team, assistant) == 200
    set_by_it = client.put(
        "/assistant/rules", json=rules, headers=_as(client, assistant)
    )
    kept = client.get("/assistant/rules", headers=team["bearer"])

    assert _name(client, team) == "改了"
    assert set_by_it.status_code == 403
    assert kept.json()["data"]["allowItems"] == []


@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({}, id="nothing"),
        pytest.param({"Authorization": "Bearer not-a-token"}, id="garbage"),
        pytest.param(
            {"Authorization": f"Bearer {SANDBOX_TOKEN}"}, id="development override"
        ),
    ],
)
def test_without_a_persons_sign_in_it_is_no_one(client, team, headers):
    assert _rename(client, team, headers) == 401
    assert (
        client.get("/users/me/team-invitations", headers=_as(client, headers))
    ).status_code == 401
    assert _name(client, team) == "原名"
