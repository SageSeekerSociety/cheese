"""A personal team is one person's: nobody but its owner is ever in one.

Its owner can neither add anyone directly nor invite anyone, an invitation sent
before that was refused cannot be accepted, and it can be reached by no link —
because it has none. The database refuses the row itself, not just the routes
that would write it, so no path can go around the rule. A shared team takes
members the same ways as before.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.domain.team.models import (
    ApplicationStatus,
    ApplicationType,
    TeamMembershipApplication,
    TeamUserRelation,
)
from tests.integration.conftest import UserCreator, unique_int


def auth(user) -> dict:
    return {"Authorization": f"Bearer {user.token}"}


@pytest.fixture
def people(user_client: UserCreator, api_client: TestClient) -> dict:
    out = {}
    for role in ("owner", "friend"):
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        out[role] = user
    return out


def my_teams(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get("/teams/my-teams", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["teams"]


def personal_team_of(api_client: TestClient, user) -> dict:
    return next(t for t in my_teams(api_client, user) if t["personal"])


def member_ids(api_client: TestClient, team_id: int, user) -> set[int]:
    resp = api_client.get(f"/teams/{team_id}/members", headers=auth(user))
    assert resp.status_code == 200, resp.text
    return {m["user"]["id"] for m in resp.json()["data"]["members"]}


def pending_invitations(api_client: TestClient, user) -> list[dict]:
    resp = api_client.get(
        "/users/me/team-invitations",
        params={"status": "PENDING"},
        headers=auth(user),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["invitations"]


def test_the_owner_cannot_add_anyone_to_a_personal_team(api_client, people):
    team = personal_team_of(api_client, people["owner"])
    resp = api_client.post(
        f"/teams/{team['id']}/members",
        json={"userId": people["friend"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 400, resp.text
    assert "personal team" in resp.json()["error"]["message"]
    assert member_ids(api_client, team["id"], people["owner"]) == {
        people["owner"].user_id
    }
    assert team["id"] not in {t["id"] for t in my_teams(api_client, people["friend"])}


def test_the_owner_cannot_invite_anyone_to_a_personal_team(api_client, people):
    team = personal_team_of(api_client, people["owner"])
    resp = api_client.post(
        f"/teams/{team['id']}/invitations",
        json={"userId": people["friend"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 400, resp.text
    assert pending_invitations(api_client, people["friend"]) == []


def test_an_invitation_sent_earlier_cannot_be_accepted(
    api_client, people, db_session, _portal
):
    team = personal_team_of(api_client, people["owner"])

    async def invite_the_old_way() -> int:
        now = datetime.now(UTC)
        app = TeamMembershipApplication(
            user_id=people["friend"].user_id,
            team_id=team["id"],
            initiator_id=people["owner"].user_id,
            type=ApplicationType.INVITATION.value,
            status=ApplicationStatus.PENDING.value,
            role="MEMBER",
            message="",
            created_at=now,
            updated_at=now,
        )
        db_session.add(app)
        await db_session.flush()
        return app.id

    invitation_id = _portal.call(invite_the_old_way)
    resp = api_client.post(
        f"/users/me/team-invitations/{invitation_id}/accept",
        headers=auth(people["friend"]),
    )
    assert resp.status_code == 400, resp.text
    assert member_ids(api_client, team["id"], people["owner"]) == {
        people["owner"].user_id
    }


def test_a_shared_team_still_takes_members(api_client, people, user_client):
    resp = api_client.post(
        "/teams",
        json={
            "name": f"Shared Team {unique_int()}",
            "intro": "",
            "description": "",
            "avatarId": 1,
        },
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text
    team_id = resp.json()["data"]["team"]["id"]

    resp = api_client.post(
        f"/teams/{team_id}/members",
        json={"userId": people["friend"].user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text

    invitee = user_client.create_user()
    invitee.token = user_client.login(api_client, invitee.username, invitee.password)
    resp = api_client.post(
        f"/teams/{team_id}/invitations",
        json={"userId": invitee.user_id, "role": "MEMBER"},
        headers=auth(people["owner"]),
    )
    assert resp.status_code == 201, resp.text
    invitation_id = resp.json()["data"]["invitation"]["id"]
    resp = api_client.post(
        f"/users/me/team-invitations/{invitation_id}/accept",
        headers=auth(invitee),
    )
    assert resp.status_code == 204, resp.text

    assert member_ids(api_client, team_id, people["owner"]) == {
        people["owner"].user_id,
        people["friend"].user_id,
        invitee.user_id,
    }


def test_the_database_refuses_a_live_member_row(
    api_client, people, db_session, _portal
):
    """The deliberate violation: the row is written straight into the table,
    with no route or service in the way, and the database itself says no.

    The routes are one gate; this is the one nothing can go around — a script,
    a backfill, or a path nobody has written yet.
    """
    team = personal_team_of(api_client, people["owner"])

    async def write_it() -> None:
        """Inside its own savepoint, so the refusal leaves the session usable."""
        now = datetime.now(UTC)
        async with db_session.begin_nested():
            db_session.add(
                TeamUserRelation(
                    team_id=team["id"],
                    user_id=people["friend"].user_id,
                    role=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db_session.flush()

    with pytest.raises(IntegrityError, match="a personal team takes no members"):
        _portal.call(write_it)

    assert member_ids(api_client, team["id"], people["owner"]) == {
        people["owner"].user_id
    }


def test_a_personal_team_has_no_join_link(api_client, people):
    """Even a token written onto one opens nothing: the link is a bad link."""
    team = personal_team_of(api_client, people["owner"])
    assert (
        api_client.get(
            f"/teams/{team['id']}/join-link", headers=auth(people["owner"])
        ).status_code
        == 400
    )
    assert (
        api_client.patch(
            f"/teams/{team['id']}/join-link",
            json={"approval": False},
            headers=auth(people["owner"]),
        ).status_code
        == 400
    )


def test_a_personal_team_cannot_recruit(api_client, people):
    """An ad asking people to join a team nobody can join is refused.

    Someone outside the team is answered the same 403 a shared team gives
    them: the refusal must not tell them what kind of team they guessed the id
    of.
    """
    team = personal_team_of(api_client, people["owner"])
    refused = api_client.post(
        f"/teams/{team['id']}/recruitment",
        json={"title": "Join us", "content": "Please"},
        headers=auth(people["owner"]),
    )
    assert refused.status_code == 400, refused.text

    outsider = api_client.post(
        f"/teams/{team['id']}/recruitment",
        json={"title": "Join us", "content": "Please"},
        headers=auth(people["friend"]),
    )
    assert outsider.status_code == 403, outsider.text


def test_the_personal_team_rows_in_the_database_are_only_its_owner(
    api_client, people, db_session, _portal
):
    """What the rule looks like from inside: one live row, the owner's."""
    team = personal_team_of(api_client, people["owner"])

    async def rows() -> list[tuple[int, int]]:
        return [
            (rel.user_id, rel.role)
            for rel in (
                await db_session.scalars(
                    select(TeamUserRelation).where(
                        TeamUserRelation.team_id == team["id"],
                        TeamUserRelation.deleted_at.is_(None),
                    )
                )
            )
        ]

    assert _portal.call(rows) == [(people["owner"].user_id, 0)]
