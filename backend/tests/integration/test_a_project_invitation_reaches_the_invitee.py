"""A project invitation reaches the person invited, where they can answer it.

The invitee is not on the project's roster yet, so nothing inside the project is
theirs to open. What they do have is their own 待办: the invitation waits in
等你处理 (``GET /awaiting-me``) naming the project and who invited them, and
once it is answered or withdrawn it is no longer there. The notice that called
them back is not in the feed as well, and does not light its unread count.

Every assertion reads what the invitee's browser would receive.
"""

import asyncio
from datetime import UTC, datetime

from tests.conftest import seed_user
from tests.integration.test_team_member_enters_team_project import _bearer
from tests.integration.test_who_may_read_a_project import _project

OWNER = "alice"
INVITEE = "invitee-1"


def _headers(client, handle: str) -> dict[str, str]:
    return _bearer(seed_user(client, handle))


def _give_a_name(client, handle: str, nickname: str) -> None:
    """A profile, as every signed-up account has: the bell names people by it."""
    from app.domain.user.models import UserProfile
    from app.domain.user.repositories import UserRepository

    seed_user(client, handle)

    async def _seed() -> None:
        async with client.test_factory() as session:
            user = await UserRepository(session).get_by_username(handle)
            assert user is not None
            now = datetime.now(UTC)
            session.add(
                UserProfile(
                    user_id=user.id,
                    nickname=nickname,
                    intro="",
                    avatar_id=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await session.commit()

    asyncio.run(_seed())


def _unread(client) -> int:
    r = client.get("/notifications/unread-count", headers=_headers(client, INVITEE))
    assert r.status_code == 200, r.text
    return r.json()["data"]["count"]


def _waiting(client) -> list[dict]:
    r = client.get("/awaiting-me", headers=_headers(client, INVITEE))
    assert r.status_code == 200, r.text
    return r.json()["data"]["data"]


def _invite(client, project_id: str) -> str:
    seed_user(client, INVITEE)
    r = client.post(
        f"/projects/{project_id}/invitations",
        json={"user_handle": INVITEE},
        headers=_headers(client, OWNER),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _answer(client, invitation_id: str, *, accept: bool) -> None:
    r = client.post(
        f"/invitations/{invitation_id}/respond",
        json={"accept": accept},
        headers=_headers(client, INVITEE),
    )
    assert r.status_code == 200, r.text


def test_the_invitee_finds_the_invitation_waiting_on_them(client):
    _give_a_name(client, OWNER, "Alice A.")
    pid, _ = _project(client, OWNER)
    before = _unread(client)

    invitation = _invite(client, pid)

    [row] = _waiting(client)
    assert row["kind"] == "project_invite"
    assert row["id"] == invitation
    assert row["placeId"] == pid
    assert row["place"]["name"] == "P"
    assert row["person"]["handle"] == OWNER
    assert row["person"]["name"] == "Alice A."
    assert _unread(client) == before


def test_an_accepted_invitation_stops_waiting(client):
    pid, _ = _project(client, OWNER)
    invitation = _invite(client, pid)

    _answer(client, invitation, accept=True)

    assert _waiting(client) == []


def test_a_declined_invitation_stops_waiting(client):
    pid, _ = _project(client, OWNER)
    invitation = _invite(client, pid)

    _answer(client, invitation, accept=False)

    assert _waiting(client) == []


def test_a_withdrawn_invitation_stops_waiting(client):
    pid, _ = _project(client, OWNER)
    invitation = _invite(client, pid)

    r = client.delete(f"/invitations/{invitation}", headers=_headers(client, OWNER))
    assert r.status_code == 200, r.text

    assert _waiting(client) == []
