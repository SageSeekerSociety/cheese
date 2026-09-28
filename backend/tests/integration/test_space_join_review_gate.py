"""Redeeming a code must not walk around the review gate.

``POST /spaces/join`` names no ``spaceId``, so the router-level
``require_reviewed_space`` never sees it, and ``SpaceService.join_space`` did
not look at ``review_status`` at all — an invite code was a second, quieter way
into a board nobody had cleared review for, one that answered 200 and handed
back the whole board (name, intro, announcements, templates). These tests drive
the endpoint the way a browser does, over real requests.

The code is the thing to test with, not a direct service call: the bug lived in
the seam between the route's gate (which cannot see this path) and the service
(which never asked), so only a request crosses both.
"""

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import SpaceMember
from app.domain.space.repositories import SpaceInviteCodeRepository
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _error_name(resp) -> str | None:
    return (resp.json().get("error") or {}).get("name")


def _newcomer(user_client: UserCreator, api_client: TestClient) -> tuple[object, str]:
    """A logged-in person who is in no board, made and logged in on the spot."""
    user = user_client.create_user()
    return user, _login(user_client, api_client, user)


def _create_pending_space(api_client: TestClient, token: str) -> dict:
    """A board exactly as the public creation route makes it.

    PENDING review, and holding the code its creator would hand out — this is
    the board the gate is supposed to keep everyone out of.
    """
    suffix = unique_int(10000000, 99999999)
    resp = api_client.post(
        "/spaces",
        json={
            "name": f"Pending Space ({suffix})",
            "intro": "Waiting on review.",
            "description": "A lengthy text. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()["data"]
    assert data["space"]["reviewStatus"] == "PENDING", data["space"]["reviewStatus"]
    assert data["inviteCode"] and data["inviteCode"]["code"], data["inviteCode"]
    return data


def _create_approved(api_client: TestClient, token: str) -> dict:
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Approved Space ({suffix})",
            "intro": "Cleared review.",
            "description": "A lengthy text. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _use_count(api_client: TestClient, token: str, space_id: int, code: str) -> int:
    """A code's spent-use count, read off the board's own list.

    Only usable on an APPROVED board: the review gate's owner exemption covers
    the exact ``/spaces/{id}`` path, so ``/spaces/{id}/invite-codes`` is a 404
    on a PENDING one — even to its creator. Use :func:`_use_count_of` there.
    """
    resp = api_client.get(f"/spaces/{space_id}/invite-codes", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    for row in resp.json()["data"]["inviteCodes"]:
        if row["code"] == code:
            return int(row["useCount"])
    raise AssertionError(f"code {code} is not in space {space_id}'s list")


async def _use_count_of(db: AsyncSession, code: str) -> int:
    """A code's spent-use count, asked of the table — the only way to read a
    PENDING board's code, whose list is behind the same gate."""
    repo = SpaceInviteCodeRepository(session=db)
    row = await repo.get_by_code(code)
    assert row is not None, f"{code} is not in the table"
    return int(row.use_count)


async def _member_rows(db: AsyncSession, space_id: int, user_id: int) -> int:
    """Live membership rows for the pair, asked of the table itself.

    The refusal must leave no row behind, and a 404 on the board cannot show
    that on its own — the gate answers 404 to everyone outside, member or not.
    """
    result = await db.execute(
        select(func.count())
        .select_from(SpaceMember)
        .where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )
    return int(result.scalar_one())


class TestAReviewGateAnInviteCodeCannotWalkAround:
    def test_a_pending_boards_code_is_a_404_and_lets_nobody_in(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        """The code is handed out by the creator, so it is in someone's hands
        before review has run. Redeeming it must answer exactly as the board's
        own address does — 404 — and must not put the redeemer on the roster."""
        creator, creator_token = _newcomer(user_client, api_client)
        outsider, outsider_token = _newcomer(user_client, api_client)

        created = _create_pending_space(api_client, creator_token)
        space_id = created["space"]["id"]
        code = created["inviteCode"]["code"]

        # The door the outsider is owed, as a control: the address refuses.
        assert (
            api_client.get(f"/spaces/{space_id}", headers=_auth(outsider_token))
        ).status_code == 404

        joined = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert joined.status_code == 404, joined.text
        assert _error_name(joined) == "NotFoundError", joined.text
        # 404, not 403: the answer must not confirm the board exists.
        assert "reviewStatus" not in joined.text

        # No membership row was written, and no use was spent.
        assert _portal.call(_member_rows, db_session, space_id, outsider.user_id) == 0
        assert _portal.call(_use_count_of, db_session, code) == 0

        # And nothing opened up afterwards, by either route.
        assert (
            api_client.get(f"/spaces/{space_id}", headers=_auth(outsider_token))
        ).status_code == 404

    def test_an_approved_boards_code_still_lets_a_stranger_in(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """The control for the fix: it must not have shut the ordinary door."""
        creator, creator_token = _newcomer(user_client, api_client)
        outsider, outsider_token = _newcomer(user_client, api_client)

        created = _create_approved(api_client, creator_token)
        space_id = created["space"]["id"]
        code = created["inviteCode"]["code"]

        assert (
            api_client.get(f"/spaces/{space_id}", headers=_auth(outsider_token))
        ).status_code == 404

        joined = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert joined.status_code == 200, joined.text
        assert joined.json()["data"]["space"]["id"] == space_id

        opened = api_client.get(f"/spaces/{space_id}", headers=_auth(outsider_token))
        assert opened.status_code == 200, opened.text

    def test_someone_already_in_redeems_again_without_an_error_or_a_spend(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        """A double-tapped「加入」is a no-op, and the review check sits after
        that no-op on purpose: someone already in a PENDING board — its own
        creator — must not start getting 404 from their own code."""
        creator, creator_token = _newcomer(user_client, api_client)
        outsider, outsider_token = _newcomer(user_client, api_client)

        # The creator of a pending board is in it; repeating is still a no-op.
        pending = _create_pending_space(api_client, creator_token)
        pending_code = pending["inviteCode"]["code"]
        again = api_client.post(
            "/spaces/join", json={"code": pending_code}, headers=_auth(creator_token)
        )
        assert again.status_code == 200, again.text
        assert _portal.call(_use_count_of, db_session, pending_code) == 0

        # And a member of an approved board asking twice spends one use, once.
        approved = _create_approved(api_client, creator_token)
        space_id = approved["space"]["id"]
        code = approved["inviteCode"]["code"]

        first = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert first.status_code == 200, first.text
        assert _use_count(api_client, creator_token, space_id, code) == 1

        second = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert second.status_code == 200, second.text
        assert _use_count(api_client, creator_token, space_id, code) == 1
