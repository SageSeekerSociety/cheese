"""A project invitation reaches the person invited, in the mail they can open.

The invitee is not on the project's roster yet, so a notice filed in that
project's inbox is one they have no way to read. What they do have is their own
mail: the bell at the top of every page (``GET /notifications`` and its unread
count). These pin that the invitation shows up there, and that once it is
answered or withdrawn it stops asking for an answer.

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


def _invitations_in_the_bell(client) -> list[dict]:
    r = client.get(
        "/notifications",
        params={"type": "PROJECT_INVITE"},
        headers=_headers(client, INVITEE),
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["notifications"]


def _invite(client, project_id: str) -> str:
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


def test_the_invitee_finds_the_invitation_in_their_bell(client):
    _give_a_name(client, OWNER, "Alice A.")
    pid, _ = _project(client, OWNER)
    before = _unread(client)

    invitation = _invite(client, pid)

    assert _unread(client) == before + 1
    [notice] = _invitations_in_the_bell(client)
    assert notice["read"] is False
    assert notice["contextMetadata"]["invitationId"] == invitation
    assert notice["contextMetadata"]["projectName"] == "P"
    assert "status" not in notice["contextMetadata"]
    assert notice["entities"]["project"]["id"] == pid
    assert notice["entities"]["inviter"]["handle"] == OWNER
    assert notice["entities"]["inviter"]["name"] == "Alice A."


def test_an_accepted_invitation_stops_asking(client):
    pid, _ = _project(client, OWNER)
    before = _unread(client)
    invitation = _invite(client, pid)

    _answer(client, invitation, accept=True)

    assert _unread(client) == before
    [notice] = _invitations_in_the_bell(client)
    assert notice["read"] is True
    assert notice["contextMetadata"]["status"] == "accepted"


def test_a_declined_invitation_stops_asking(client):
    pid, _ = _project(client, OWNER)
    before = _unread(client)
    invitation = _invite(client, pid)

    _answer(client, invitation, accept=False)

    assert _unread(client) == before
    [notice] = _invitations_in_the_bell(client)
    assert notice["read"] is True
    assert notice["contextMetadata"]["status"] == "declined"


def test_a_withdrawn_invitation_stops_asking(client):
    pid, _ = _project(client, OWNER)
    before = _unread(client)
    invitation = _invite(client, pid)

    r = client.delete(f"/invitations/{invitation}", headers=_headers(client, OWNER))
    assert r.status_code == 200, r.text

    assert _unread(client) == before
    [notice] = _invitations_in_the_bell(client)
    assert notice["read"] is True
    assert notice["contextMetadata"]["status"] == "revoked"
