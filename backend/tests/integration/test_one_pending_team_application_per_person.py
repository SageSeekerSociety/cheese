"""A person has one pending invitation or request per team, however fast it is sent.

The service checks for a pending row before it inserts one. Two submissions at
once — a double click on 邀请, two admins inviting the same person — both read
"nothing pending" before either has written, so the check alone let both in.
Each test here stands in for that race by letting the second submission past
the check, which is exactly where the real race leaves it, and asserts what the
database then allows.
"""

import pytest
from fastapi.testclient import TestClient

from app.domain.team.membership_services import TeamMembershipService
from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def team(user_client: UserCreator, api_client: TestClient) -> dict:
    people = {}
    for role in ("owner", "friend"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        people[role] = user
    created = api_client.post(
        "/teams",
        json={
            "name": f"Pending Once ({unique_int(10000000, 99999999)})",
            "intro": "",
            "description": "A team someone is invited into twice at once. " * 3,
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert created.status_code == 201, created.text
    return {**people, "id": created.json()["data"]["team"]["id"]}


def _past_the_check(monkeypatch) -> None:
    """Every submission from here on finds nothing pending, as the second of
    two racing submissions does."""

    async def nothing_pending(self, user_id: int, team_id: int) -> None:
        return None

    async def none(self, team_id: int, user_id: int) -> str:
        return "none"

    monkeypatch.setattr(
        TeamMembershipService, "_validate_user_can_apply_or_be_invited", nothing_pending
    )
    monkeypatch.setattr(TeamMembershipService, "join_status", none)


def _invite(api_client: TestClient, team: dict):
    return api_client.post(
        f"/teams/{team['id']}/invitations",
        json={"userId": team["friend"].user_id, "role": "MEMBER"},
        headers=auth(team["owner"]),
    )


def _pending(api_client: TestClient, team: dict, kind: str) -> list[dict]:
    resp = api_client.get(
        f"/teams/{team['id']}/{kind}",
        params={"status": "PENDING"},
        headers=auth(team["owner"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    rows = data.get("applications", data.get("invitations", []))
    return [r for r in rows if r["user"]["id"] == team["friend"].user_id]


def test_a_second_invitation_that_raced_past_the_check_is_refused(
    api_client, team, monkeypatch
):
    assert _invite(api_client, team).status_code == 201
    _past_the_check(monkeypatch)

    again = _invite(api_client, team)

    assert again.status_code == 409, again.text
    assert len(_pending(api_client, team, "invitations")) == 1


def test_an_invitation_can_be_sent_again_once_the_first_is_withdrawn(
    api_client, team
):
    assert _invite(api_client, team).status_code == 201
    [first] = _pending(api_client, team, "invitations")
    withdrawn = api_client.delete(
        f"/teams/{team['id']}/invitations/{first['id']}", headers=auth(team["owner"])
    )
    assert withdrawn.status_code in (200, 204), withdrawn.text

    assert _invite(api_client, team).status_code == 201
    assert len(_pending(api_client, team, "invitations")) == 1


def test_a_second_join_request_that_raced_past_the_check_leaves_one(
    api_client, team, monkeypatch
):
    first = api_client.post(f"/teams/{team['id']}/join", headers=auth(team["friend"]))
    assert first.status_code == 200, first.text
    _past_the_check(monkeypatch)

    again = api_client.post(f"/teams/{team['id']}/join", headers=auth(team["friend"]))

    assert again.status_code == 200, again.text
    assert len(_pending(api_client, team, "requests")) == 1
