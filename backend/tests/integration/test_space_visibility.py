"""Space visibility tiers, membership and invite codes, end to end.

Walks the acceptance list for the 空间成员与邀请码 task:

- a 凭码 space is invisible in the list and through a direct link until the
  code is redeemed, and shows up on the redeemer's list right after;
- leaving hides it again and leaves the leaver's projects alone;
- a manager whose role was revoked is refused when removing a member;
- a 私人 space refuses non-members on both /spaces/{id} and /spaces/{id}/managers;
- 公开 spaces answer exactly as they did before (regression);
- and the writes behind all of it hold up when the same request arrives twice:
  one live membership row per person, one invite-code use spent for one join,
  and a roster whose hydration does not grow with the roster.
"""

import time
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import SpaceMember
from app.domain.space.repositories import SpaceInviteCodeRepository
from tests.integration.conftest import (
    UserCreator,
    session_auth_headers,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _create_space(
    api_client: TestClient,
    token: str,
    *,
    visibility: str,
) -> dict:
    suffix = unique_int(10000000, 99999999)
    resp = api_client.post(
        "/spaces",
        json={
            "name": f"Visibility Space {visibility} ({suffix})",
            "intro": "Tier test.",
            "description": "A lengthy text. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "announcements": [],
            "taskTemplates": [],
            "visibility": visibility,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]


def _listed_space_ids(api_client: TestClient, token: str) -> set[int]:
    """Every id the viewer's space list returns, paging to the end."""
    ids: set[int] = set()
    start: int | None = 0
    for _ in range(20):
        resp = api_client.get(
            "/spaces",
            params={"pageStart": start or 0, "pageSize": 200},
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        ids.update(space["id"] for space in data["spaces"])
        if not data["page"]["hasMore"]:
            break
        start = data["page"]["nextStart"]
    return ids


def _error_name(resp) -> str | None:
    return (resp.json().get("error") or {}).get("name")


def _members(api_client: TestClient, token: str, space_id: int) -> list[dict]:
    resp = api_client.get(f"/spaces/{space_id}/members", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["members"]


def _mint_code(
    api_client: TestClient,
    token: str,
    space_id: int,
    *,
    max_uses: int = 10,
    expires_at: int | None = None,
) -> str:
    body: dict = {"maxUses": max_uses}
    if expires_at is not None:
        body["expiresAt"] = expires_at
    resp = api_client.post(
        f"/spaces/{space_id}/invite-codes", json=body, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["inviteCode"]["code"]


def _use_count(api_client: TestClient, token: str, space_id: int, code: str) -> int:
    resp = api_client.get(f"/spaces/{space_id}/invite-codes", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    for row in resp.json()["data"]["inviteCodes"]:
        if row["code"] == code:
            return int(row["useCount"])
    raise AssertionError(f"code {code} is not in the space's list")


def _join(api_client: TestClient, token: str, code: str) -> None:
    resp = api_client.post("/spaces/join", json={"code": code}, headers=_auth(token))
    assert resp.status_code == 200, resp.text


@pytest.fixture
def sql_log(db_connection):
    """Every statement that reaches the database while the context is open.

    Bound to the per-test connection — the one the TestClient's session is
    built on — so it records what the endpoints really ran, including anything
    a lazy-loading attribute fired behind their backs, which is the shape an
    N+1 takes. A counter wrapped around the repository would miss that.

    The listener goes on the sync connection under the async one: SQLAlchemy
    refuses these events on an `AsyncConnection` by name.
    """
    from sqlalchemy import event

    target = db_connection.sync_connection
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        statements.append(statement)

    event.listen(target, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(target, "before_cursor_execute", record)


def _selects(statements: list[str]) -> int:
    return sum(1 for s in statements if s.lstrip().upper().startswith("SELECT"))


class TestCodeSpaceVisibility:
    """凭码: the space is a stranger to everyone who has not redeemed a code."""

    def test_code_space_is_invisible_until_the_code_is_redeemed(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        invite = created["inviteCode"]
        assert invite is not None, "a 凭码 space must come with a code"
        code = invite["code"]
        assert created["space"]["visibility"] == "CODE"

        # Not in the list, and not through a direct link either.
        assert space_id in _listed_space_ids(api_client, creator_token)
        assert space_id not in _listed_space_ids(api_client, outsider_token)
        for path in (
            f"/spaces/{space_id}",
            f"/spaces/{space_id}/managers",
            f"/spaces/{space_id}/members",
        ):
            resp = api_client.get(path, headers=_auth(outsider_token))
            assert resp.status_code == 404, f"{path} -> {resp.status_code}"
            assert _error_name(resp) == "NotFoundError", resp.text

        # Redeem.
        join = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert join.status_code == 200, join.text
        assert join.json()["data"]["space"]["id"] == space_id

        # Visible at once, and on their list (the home page).
        detail = api_client.get(f"/spaces/{space_id}", headers=_auth(outsider_token))
        assert detail.status_code == 200, detail.text
        assert space_id in _listed_space_ids(api_client, outsider_token)
        managers = api_client.get(
            f"/spaces/{space_id}/managers", headers=_auth(outsider_token)
        )
        assert managers.status_code == 200, managers.text

        code_row = api_client.get(
            f"/spaces/{space_id}/invite-codes", headers=_auth(creator_token)
        )
        assert code_row.status_code == 200, code_row.text
        assert code_row.json()["data"]["inviteCodes"][0]["useCount"] == 1

    def test_leaving_hides_the_space_but_keeps_the_projects(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        member = user_client.create_user()
        member_token = _login(user_client, api_client, member)

        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        code = created["inviteCode"]["code"]
        api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(member_token)
        )
        assert space_id in _listed_space_ids(api_client, member_token)

        # A project of their own, made while a member.
        project = api_client.post(
            "/projects",
            json={"name": f"Kept Project ({unique_int(1000, 9999)})"},
            headers=session_auth_headers(member.username),
        )
        assert project.status_code == 200, project.text
        project_id = project.json()["data"]["id"]

        leave = api_client.post(
            f"/spaces/{space_id}/leave", headers=_auth(member_token)
        )
        assert leave.status_code == 204, leave.text

        assert space_id not in _listed_space_ids(api_client, member_token)
        gone = api_client.get(f"/spaces/{space_id}", headers=_auth(member_token))
        assert gone.status_code == 404, gone.text

        # The project is not the space's to take away.
        listed = api_client.get(
            "/projects", headers=session_auth_headers(member.username)
        )
        assert listed.status_code == 200, listed.text
        ids = [p["id"] for p in listed.json()["data"]["data"]]
        assert project_id in ids, listed.text

    def test_exhausted_and_expired_codes_are_refused(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)

        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]

        single = api_client.post(
            f"/spaces/{space_id}/invite-codes",
            json={"maxUses": 1},
            headers=_auth(creator_token),
        )
        assert single.status_code == 201, single.text
        code = single.json()["data"]["inviteCode"]["code"]

        first = user_client.create_user()
        first_token = _login(user_client, api_client, first)
        second = user_client.create_user()
        second_token = _login(user_client, api_client, second)

        ok = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(first_token)
        )
        assert ok.status_code == 200, ok.text

        exhausted = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(second_token)
        )
        assert exhausted.status_code == 400, exhausted.text
        assert _error_name(exhausted) == "BadRequestError", exhausted.text

        stale = api_client.post(
            f"/spaces/{space_id}/invite-codes",
            json={"maxUses": 5, "expiresAt": int(time.time() * 1000) - 86_400_000},
            headers=_auth(creator_token),
        )
        assert stale.status_code == 201, stale.text
        stale_code = stale.json()["data"]["inviteCode"]["code"]
        third = user_client.create_user()
        third_token = _login(user_client, api_client, third)
        expired = api_client.post(
            "/spaces/join", json={"code": stale_code}, headers=_auth(third_token)
        )
        assert expired.status_code == 400, expired.text

    def test_a_code_does_not_open_a_space_that_is_not_凭码(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token, visibility="PUBLIC")
        space_id = created["space"]["id"]
        assert created["inviteCode"] is None

        minted = api_client.post(
            f"/spaces/{space_id}/invite-codes",
            json={"maxUses": 5},
            headers=_auth(creator_token),
        )
        assert minted.status_code == 201, minted.text
        code = minted.json()["data"]["inviteCode"]["code"]

        resp = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert resp.status_code == 400, resp.text
        assert _error_name(resp) == "BadRequestError", resp.text


class TestPrivateSpaceVisibility:
    """私人: only the people who were added."""

    def test_a_private_space_refuses_non_members_on_detail_and_managers(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        guest = user_client.create_user()
        guest_token = _login(user_client, api_client, guest)

        created = _create_space(api_client, creator_token, visibility="PRIVATE")
        space_id = created["space"]["id"]
        assert created["space"]["visibility"] == "PRIVATE"
        assert created["inviteCode"] is None, "私人 has no code to hand out"
        assert space_id not in _listed_space_ids(api_client, guest_token)

        for path in (f"/spaces/{space_id}", f"/spaces/{space_id}/managers"):
            resp = api_client.get(path, headers=_auth(guest_token))
            assert resp.status_code == 404, f"{path} -> {resp.status_code}"
            assert _error_name(resp) == "NotFoundError", resp.text

        # The creator adds them; now both routes answer — and their own
        # managers listing is the creator's doing, not theirs.
        added = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": guest.user_id},
            headers=_auth(creator_token),
        )
        assert added.status_code == 201, added.text
        assert added.json()["data"]["member"]["userId"] == guest.user_id

        assert (
            api_client.get(
                f"/spaces/{space_id}", headers=_auth(guest_token)
            ).status_code
            == 200
        )
        assert (
            api_client.get(
                f"/spaces/{space_id}/managers", headers=_auth(guest_token)
            ).status_code
            == 200
        )
        assert space_id in _listed_space_ids(api_client, guest_token)

    def test_a_stranger_cannot_ask_to_be_added(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        stranger = user_client.create_user()
        stranger_token = _login(user_client, api_client, stranger)

        created = _create_space(api_client, creator_token, visibility="PRIVATE")
        space_id = created["space"]["id"]

        resp = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": stranger.user_id},
            headers=_auth(stranger_token),
        )
        assert resp.status_code == 403, resp.text
        assert _error_name(resp) == "ForbiddenError", resp.text


class TestSpaceManagerPermissions:
    """Who may remove whom, and what a revoked role can no longer do."""

    def test_a_revoked_manager_is_refused_when_removing_a_member(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        manager = user_client.create_user()
        manager_token = _login(user_client, api_client, manager)
        member = user_client.create_user()

        created = _create_space(api_client, creator_token, visibility="PUBLIC")
        space_id = created["space"]["id"]

        granted = api_client.post(
            f"/spaces/{space_id}/managers",
            json={"userId": manager.user_id, "role": "ADMIN"},
            headers=_auth(creator_token),
        )
        assert granted.status_code == 201, granted.text

        added = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": member.user_id},
            headers=_auth(creator_token),
        )
        assert added.status_code == 201, added.text

        # While the role stands, an admin may remove a member.
        removed = api_client.delete(
            f"/spaces/{space_id}/members/{member.user_id}",
            headers=_auth(manager_token),
        )
        assert removed.status_code == 204, removed.text
        api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": member.user_id},
            headers=_auth(creator_token),
        )

        revoked = api_client.delete(
            f"/spaces/{space_id}/managers/{manager.user_id}",
            headers=_auth(creator_token),
        )
        assert revoked.status_code == 204, revoked.text

        refused = api_client.delete(
            f"/spaces/{space_id}/members/{member.user_id}",
            headers=_auth(manager_token),
        )
        assert refused.status_code == 403, refused.text
        assert _error_name(refused) == "ForbiddenError", refused.text

        # And nothing happened: the member is still there.
        listed = api_client.get(
            f"/spaces/{space_id}/members", headers=_auth(creator_token)
        )
        assert listed.status_code == 200, listed.text
        assert member.user_id in [m["userId"] for m in listed.json()["data"]["members"]]

    def test_an_admin_cannot_revoke_another_admin(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        first = user_client.create_user()
        first_token = _login(user_client, api_client, first)
        second = user_client.create_user()

        created = _create_space(api_client, creator_token, visibility="PUBLIC")
        space_id = created["space"]["id"]
        for user in (first, second):
            resp = api_client.post(
                f"/spaces/{space_id}/managers",
                json={"userId": user.user_id, "role": "ADMIN"},
                headers=_auth(creator_token),
            )
            assert resp.status_code == 201, resp.text

        resp = api_client.delete(
            f"/spaces/{space_id}/managers/{second.user_id}",
            headers=_auth(first_token),
        )
        assert resp.status_code == 403, resp.text
        assert _error_name(resp) == "ForbiddenError", resp.text


class TestPublicSpaceRegression:
    """公开: exactly what it was before this change."""

    def test_a_public_space_still_answers_any_signed_in_user(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        passerby = user_client.create_user()
        passerby_token = _login(user_client, api_client, passerby)

        created = _create_space(api_client, creator_token, visibility="PUBLIC")
        space_id = created["space"]["id"]
        assert created["space"]["visibility"] == "PUBLIC"

        assert space_id in _listed_space_ids(api_client, passerby_token)
        assert (
            api_client.get(
                f"/spaces/{space_id}", headers=_auth(passerby_token)
            ).status_code
            == 200
        )
        managers = api_client.get(
            f"/spaces/{space_id}/managers", headers=_auth(passerby_token)
        )
        assert managers.status_code == 200, managers.text
        assert [m["userId"] for m in managers.json()["data"]["managers"]] == [
            creator.user_id
        ]
        members = api_client.get(
            f"/spaces/{space_id}/members", headers=_auth(passerby_token)
        )
        assert members.status_code == 200, members.text
        assert members.json()["data"]["members"] == []

    def test_visibility_defaults_to_public_when_unstated(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        token = _login(user_client, api_client, creator)
        suffix = unique_int(10000000, 99999999)
        resp = api_client.post(
            "/spaces",
            json={
                "name": f"Legacy Payload Space ({suffix})",
                "intro": "No visibility field at all.",
                "description": "A lengthy text. " * 100,
                "avatarId": 1,
                "enableRank": False,
                "announcements": [],
                "taskTemplates": [],
            },
            headers=_auth(token),
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["space"]["visibility"] == "PUBLIC"
        assert resp.json()["data"]["inviteCode"] is None


class TestSideDoors:
    """Hiding the list is not enough — a direct link must not read either.

    Every space-scoped read route, not just the three the tier was written
    on: a 私人 space that answers /spaces/{id}/topics to a stranger is
    「只藏列表」with extra steps.
    """

    #: Space-scoped reads that check nothing of their own beyond a login.
    SIDE_DOORS = (
        "/categories",
        "/topics",
        "/domain-groups",
        "/analytics/overview",
        "/analytics/alerts",
        "/analytics/publishers",
        "/analytics/participants",
        "/analytics/tasks",
        "/me/publishing",
        "/me/participating",
    )

    def _assert_every_door_is_shut(
        self, api_client: TestClient, space_id: int, token: str
    ) -> None:
        for suffix in self.SIDE_DOORS:
            resp = api_client.get(f"/spaces/{space_id}{suffix}", headers=_auth(token))
            assert resp.status_code == 404, (
                f"{suffix} -> {resp.status_code}: {resp.text}"
            )
            assert _error_name(resp) == "NotFoundError", resp.text

    def test_a_private_space_leaks_through_no_sub_resource(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token, visibility="PRIVATE")
        space_id = created["space"]["id"]

        self._assert_every_door_is_shut(api_client, space_id, outsider_token)

        # And the owner is not shut out of their own space by the same gate.
        for suffix in ("/categories", "/topics"):
            resp = api_client.get(
                f"/spaces/{space_id}{suffix}", headers=_auth(creator_token)
            )
            assert resp.status_code == 200, (
                f"{suffix} -> {resp.status_code}: {resp.text}"
            )

    def test_a_code_space_leaks_through_no_sub_resource_before_redemption(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        code = created["inviteCode"]["code"]

        self._assert_every_door_is_shut(api_client, space_id, outsider_token)

        joined = api_client.post(
            "/spaces/join", json={"code": code}, headers=_auth(outsider_token)
        )
        assert joined.status_code == 200, joined.text

        # Redeeming the code is the door: the same routes now answer.
        for suffix in ("/categories", "/topics"):
            resp = api_client.get(
                f"/spaces/{space_id}{suffix}", headers=_auth(outsider_token)
            )
            assert resp.status_code == 200, (
                f"{suffix} -> {resp.status_code}: {resp.text}"
            )

    def test_a_removed_member_stops_seeing_the_space(
        self, user_client: UserCreator, api_client: TestClient
    ):
        """剔除 only decides who the space is visible to — and that it decides."""
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        member = user_client.create_user()
        member_token = _login(user_client, api_client, member)

        created = _create_space(api_client, creator_token, visibility="PRIVATE")
        space_id = created["space"]["id"]

        added = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": member.user_id},
            headers=_auth(creator_token),
        )
        assert added.status_code == 201, added.text
        assert (
            api_client.get(
                f"/spaces/{space_id}", headers=_auth(member_token)
            ).status_code
            == 200
        )
        assert space_id in _listed_space_ids(api_client, member_token)

        removed = api_client.delete(
            f"/spaces/{space_id}/members/{member.user_id}",
            headers=_auth(creator_token),
        )
        assert removed.status_code == 204, removed.text

        # Gone from the list, gone through the link, gone from the side doors.
        assert space_id not in _listed_space_ids(api_client, member_token)
        assert (
            api_client.get(
                f"/spaces/{space_id}", headers=_auth(member_token)
            ).status_code
            == 404
        )
        self._assert_every_door_is_shut(api_client, space_id, member_token)

        # The creator still runs it.
        assert (
            api_client.get(
                f"/spaces/{space_id}", headers=_auth(creator_token)
            ).status_code
            == 200
        )


async def _rows_for_pair(db: AsyncSession, space_id: int, user_id: int) -> int:
    """Rows in the table for the pair — deleted ones included. The point of
    the revive path is that this stays 1 across joins, leaves and re-joins."""
    result = await db.execute(
        select(func.count())
        .select_from(SpaceMember)
        .where(SpaceMember.space_id == space_id, SpaceMember.user_id == user_id)
    )
    return int(result.scalar_one())


async def _insert_live_row(db: AsyncSession, space_id: int, user_id: int) -> None:
    """Write a second live membership the way the application writes one.

    Inside its own savepoint so the refusal leaves the session usable and the
    test can keep asking it questions afterwards.
    """
    now = datetime.now(UTC)
    async with db.begin_nested():
        db.add(
            SpaceMember(
                space_id=space_id,
                user_id=user_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
        )
        await db.flush()


async def _spend(db: AsyncSession, code: str) -> bool:
    repo = SpaceInviteCodeRepository(session=db)
    row = await repo.get_by_code(code)
    assert row is not None, f"{code} is not in the table"
    return await repo.consume_use(row.id)


async def _use_count_of(db: AsyncSession, code: str) -> int:
    repo = SpaceInviteCodeRepository(session=db)
    row = await repo.get_by_code(code)
    assert row is not None, f"{code} is not in the table"
    return int(row.use_count)


class TestMembershipIsOneRow:
    """The writes behind「加入」, and what makes repeating them safe.

    One live row per (space, person) is a property the database has to hold,
    not one a reader can establish: two requests for the same person really do
    arrive together — a double-tapped「加入」, or a code redeemed while an admin
    adds that same person — and a read-then-write pair lets both read "not a
    member" and both write. That spends an extra invite-code use at best, and
    at worst leaves two live rows for the pair, which turns `get_member`'s
    `scalar_one_or_none` into `MultipleResultsFound`: the documented
    double-tap no-op becoming a 500 on every later check of that membership.
    """

    def test_joining_twice_keeps_one_row_and_spends_one_use(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        code = _mint_code(api_client, creator_token, space_id, max_uses=5)

        joiner = user_client.create_user()
        joiner_token = _login(user_client, api_client, joiner)

        # The double tap the tier was written for. The second redemption is a
        # no-op, and「no-op」has to include the use counter — otherwise a code
        # runs out because people were enthusiastic, which is the one thing a
        # no-op must not do.
        _join(api_client, joiner_token, code)
        _join(api_client, joiner_token, code)

        assert [
            member["userId"] for member in _members(api_client, creator_token, space_id)
        ] == [joiner.user_id]
        assert _use_count(api_client, creator_token, space_id, code) == 1
        # Still answerable: `get_member` is what every visibility check runs
        # on, so a duplicated row would surface here rather than in the list.
        assert space_id in _listed_space_ids(api_client, joiner_token)

    def test_rejoining_after_leaving_revives_that_row(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        code = _mint_code(api_client, creator_token, space_id, max_uses=5)

        joiner = user_client.create_user()
        joiner_token = _login(user_client, api_client, joiner)
        _join(api_client, joiner_token, code)
        assert (
            api_client.post(
                f"/spaces/{space_id}/leave", headers=_auth(joiner_token)
            ).status_code
            == 204
        )

        _join(api_client, joiner_token, code)

        assert [
            member["userId"] for member in _members(api_client, creator_token, space_id)
        ] == [joiner.user_id]
        # Re-joining spends a use — it is a join, not a no-op — while the
        # table keeps one row for the pair rather than one per attempt.
        assert _use_count(api_client, creator_token, space_id, code) == 2
        assert _portal.call(_rows_for_pair, db_session, space_id, joiner.user_id) == 1

    def test_the_database_refuses_a_second_live_row(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        """Uniqueness is a constraint, not a convention.

        The second write below is a well-formed membership row for a pair that
        already has one, written exactly the way the application writes them.
        What refuses it is the database, so no amount of request interleaving
        can get past it — which is the whole difference between this and the
        read-then-write check that a concurrent request can pass too.
        """
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        code = created["inviteCode"]["code"]

        joiner = user_client.create_user()
        _join(api_client, _login(user_client, api_client, joiner), code)

        with pytest.raises(IntegrityError):
            _portal.call(_insert_live_row, db_session, space_id, joiner.user_id)

        # The refused row is not there, so the space still answers with the
        # one membership it had.
        assert _portal.call(_rows_for_pair, db_session, space_id, joiner.user_id) == 1

    def test_a_removed_member_can_be_added_back(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        """剔除 then 添加 is one row again, not two.

        The re-add arrives through the other caller — an admin putting someone
        back, not the person redeeming a code — and has to land on the same
        revive path.
        """
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token, visibility="PRIVATE")
        space_id = created["space"]["id"]
        member = user_client.create_user()

        for _ in range(2):
            added = api_client.post(
                f"/spaces/{space_id}/members",
                json={"userId": member.user_id},
                headers=_auth(creator_token),
            )
            assert added.status_code == 201, added.text
            removed = api_client.delete(
                f"/spaces/{space_id}/members/{member.user_id}",
                headers=_auth(creator_token),
            )
            assert removed.status_code == 204, removed.text

        # The last removal stands…
        assert _members(api_client, creator_token, space_id) == []
        added = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": member.user_id},
            headers=_auth(creator_token),
        )
        assert added.status_code == 201, added.text

        # …and putting them back three times over is still one row.
        assert [
            entry["userId"] for entry in _members(api_client, creator_token, space_id)
        ] == [member.user_id]
        assert _portal.call(_rows_for_pair, db_session, space_id, member.user_id) == 1

    def test_an_expired_code_cannot_be_spent(
        self,
        user_client: UserCreator,
        api_client: TestClient,
        db_session: AsyncSession,
        _portal,
    ):
        """Expiry is checked in the statement that spends a use, not only in
        the read before it.

        `join_space` reads the code, decides, and then spends — so a code that
        expires in between is honoured by a read-only check, and the window is
        however long the request takes, not zero. Reached here through the
        repository directly because the endpoint refuses an already-expired
        code at the read above, which is exactly why the second check needs
        its own test.
        """
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        stale = _mint_code(
            api_client,
            creator_token,
            space_id,
            max_uses=5,
            expires_at=int(time.time() * 1000) - 60_000,
        )

        assert _portal.call(_spend, db_session, stale) is False
        assert _portal.call(_use_count_of, db_session, stale) == 0

    def test_hydrating_the_roster_does_not_grow_with_the_roster(
        self, user_client: UserCreator, api_client: TestClient, sql_log: list[str]
    ):
        """Two queries for the display names, however many members there are.

        The hydration used to be a `get_by_id` + `get_profile_by_user_id` pair
        per row, inside the loop building the list — so a space with a class
        in it cost two queries per student, on every read of it, and the count
        grew with the roster. Measured at two sizes in ONE test on purpose: a
        fixed number would only pin today's endpoint, while comparing 1 member
        against 5 pins the thing that matters — that the count does not move.
        """
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token, visibility="CODE")
        space_id = created["space"]["id"]
        code = _mint_code(api_client, creator_token, space_id, max_uses=20)

        first = user_client.create_user()
        _join(api_client, _login(user_client, api_client, first), code)
        # Warm-up, so the measured calls are not the ones paying for anything
        # the process does once.
        api_client.get(f"/spaces/{space_id}/members", headers=_auth(creator_token))
        sql_log.clear()
        api_client.get(f"/spaces/{space_id}/members", headers=_auth(creator_token))
        one_member = _selects(sql_log)

        for _ in range(4):
            extra = user_client.create_user()
            _join(api_client, _login(user_client, api_client, extra), code)
        sql_log.clear()
        api_client.get(f"/spaces/{space_id}/members", headers=_auth(creator_token))
        five_members = _selects(sql_log)

        assert len(_members(api_client, creator_token, space_id)) == 5
        assert five_members == one_member, (
            f"{one_member} selects for 1 member but {five_members} for 5 — the "
            "hydration is per row again"
        )
