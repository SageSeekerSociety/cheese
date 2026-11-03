"""Membership in a 题目版, end to end.

There is no visibility tier. Who can see a 题目版 exists is answered by
membership and nothing else, so the acceptance list is short:

- a 题目版 you did not create and have not joined is absent from your list
  and 404 through a direct link, including every sub-resource;
- joining through the board's link is the ordinary way in — the board shows
  up on the joiner's list immediately after (the link itself is
  `test_space_join_link.py`'s);
- the owner can also put someone in directly;
- leaving (or being removed) hides it again and takes nothing else away — not
  the leaver's projects;
- and the writes behind「加入」hold up when the same request arrives twice:
  one live membership row per person, and a roster whose hydration does not
  grow with the roster.
"""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.space.models import SpaceMember
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    post_project,
    session_auth_headers,
    unique_int,
)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(user_client: UserCreator, api_client: TestClient, user) -> str:
    return user_client.login(api_client, user.username, user.password)


def _create_space(api_client: TestClient, token: str) -> dict:
    """A 题目版 that has cleared review.

    Review is a separate axis and it gates everyone — a PENDING board is in
    nobody's list — so these tests start from an approved one or they would be
    measuring the review gate instead.
    """
    suffix = unique_int(10000000, 99999999)
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Membership Space ({suffix})",
            "intro": "Membership test.",
            "description": "A lengthy text. " * 100,
            "avatarId": 1,
            "enableRank": False,
            "taskTemplates": [],
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


def _member_row(
    api_client: TestClient, token: str, space_id: int, user_id: int
) -> dict:
    """One roster row, by the person it is about — never by index.

    A board's creator is a member of the board they made, so the roster is
    never just the people a test put in it.
    """
    for row in _members(api_client, token, space_id):
        if row["userId"] == user_id:
            return row
    raise AssertionError(f"user {user_id} is not on space {space_id}'s roster")


def _link(api_client: TestClient, token: str, space_id: int) -> str:
    """The board's join link token, as its owner or an admin reads it."""
    resp = api_client.get(f"/spaces/{space_id}/join-link", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["token"]


def _try_join(api_client: TestClient, token: str, link: str):
    return api_client.post(f"/space-invites/{link}/join", headers=_auth(token))


def _join(api_client: TestClient, token: str, link: str) -> None:
    resp = _try_join(api_client, token, link)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["joinStatus"] == "member", resp.text


def _newcomer(user_client: UserCreator, api_client: TestClient) -> str:
    """A logged-in user who is not in any board yet."""
    return _login(user_client, api_client, user_client.create_user())


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

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(target, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(target, "before_cursor_execute", record)


def _selects(statements: list[str]) -> int:
    return sum(1 for s in statements if s.lstrip().upper().startswith("SELECT"))


class TestABoardIsInvisibleUntilYouAreInIt:
    """The whole rule: not a member, not in the list, not at its address."""

    def test_it_is_a_stranger_to_everyone_who_did_not_join(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]

        # The creator runs it without anyone having written a member row.
        assert space_id in _listed_space_ids(api_client, creator_token)

        # Nobody else sees it, by either route.
        assert space_id not in _listed_space_ids(api_client, outsider_token)
        for path in (
            f"/spaces/{space_id}",
            f"/spaces/{space_id}/managers",
            f"/spaces/{space_id}/members",
        ):
            resp = api_client.get(path, headers=_auth(outsider_token))
            assert resp.status_code == 404, f"{path} -> {resp.status_code}"
            assert _error_name(resp) == "NotFoundError", resp.text

    def test_joining_through_the_link_is_what_puts_it_on_your_list(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        joiner = user_client.create_user()
        joiner_token = _login(user_client, api_client, joiner)

        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)
        assert space_id not in _listed_space_ids(api_client, joiner_token)

        join = _try_join(api_client, joiner_token, link)
        assert join.status_code == 200, join.text
        assert join.json()["data"]["space"]["id"] == space_id

        assert space_id in _listed_space_ids(api_client, joiner_token)
        detail = api_client.get(f"/spaces/{space_id}", headers=_auth(joiner_token))
        assert detail.status_code == 200, detail.text
        assert (
            api_client.get(
                f"/spaces/{space_id}/managers", headers=_auth(joiner_token)
            ).status_code
            == 200
        )


class TestLeavingAndBeingRemoved:
    """Both take the board away, and neither takes anything else."""

    def test_leaving_hides_the_space_but_keeps_the_projects(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        member = user_client.create_user()
        member_token = _login(user_client, api_client, member)

        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)
        _try_join(api_client, member_token, link)
        assert space_id in _listed_space_ids(api_client, member_token)

        # A project of their own, made while a member.
        project = post_project(
            api_client,
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

        # The project is not the board's to take away.
        listed = api_client.get(
            "/projects", headers=session_auth_headers(member.username)
        )
        assert listed.status_code == 200, listed.text
        ids = [p["id"] for p in listed.json()["data"]["data"]]
        assert project_id in ids, listed.text


class TestTheOwnerCanAddDirectly:
    """The other way in, for when only one person should get in."""

    def test_an_added_member_sees_it_and_a_stranger_still_does_not(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        guest = user_client.create_user()
        guest_token = _login(user_client, api_client, guest)
        stranger = user_client.create_user()
        stranger_token = _login(user_client, api_client, stranger)

        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        assert space_id not in _listed_space_ids(api_client, guest_token)

        for path in (f"/spaces/{space_id}", f"/spaces/{space_id}/managers"):
            resp = api_client.get(path, headers=_auth(guest_token))
            assert resp.status_code == 404, f"{path} -> {resp.status_code}"
            assert _error_name(resp) == "NotFoundError", resp.text

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
        assert space_id in _listed_space_ids(api_client, guest_token)

        # Adding is the owner's call, not anyone's.
        resp = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": stranger.user_id},
            headers=_auth(stranger_token),
        )
        assert resp.status_code == 404, resp.text


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

        created = _create_space(api_client, creator_token)
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

        # Revoked means out, not merely demoted: a 题目版 a former admin no
        # longer administers is not one they can see at all, so the refusal
        # arrives as 404 rather than 403.
        refused = api_client.delete(
            f"/spaces/{space_id}/members/{member.user_id}",
            headers=_auth(manager_token),
        )
        assert refused.status_code == 404, refused.text
        assert _error_name(refused) == "NotFoundError", refused.text

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

        created = _create_space(api_client, creator_token)
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
        assert _error_name(resp) == "AccessDeniedError", resp.text


class TestSideDoors:
    """Hiding the list is not enough — a direct link must not read either.

    Every space-scoped read route, not just the three the rule was written
    on: a 题目版 that answers /spaces/{id}/topics to an outsider is
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
        "/analytics/people",
        "/analytics/tasks",
        "/me/publishing/tasks",
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

    def test_an_outsider_leaks_through_no_sub_resource(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]

        self._assert_every_door_is_shut(api_client, space_id, outsider_token)

        # And the owner is not shut out of their own board by the same gate.
        for suffix in ("/categories", "/topics"):
            resp = api_client.get(
                f"/spaces/{space_id}{suffix}", headers=_auth(creator_token)
            )
            assert resp.status_code == 200, (
                f"{suffix} -> {resp.status_code}: {resp.text}"
            )

    def test_joining_opens_every_door(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        outsider = user_client.create_user()
        outsider_token = _login(user_client, api_client, outsider)

        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)

        self._assert_every_door_is_shut(api_client, space_id, outsider_token)

        joined = _try_join(api_client, outsider_token, link)
        assert joined.status_code == 200, joined.text

        # Joining is the door: the same routes now answer.
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
        """剔除 only decides who the board is visible to — and that it decides."""
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        member = user_client.create_user()
        member_token = _login(user_client, api_client, member)

        created = _create_space(api_client, creator_token)
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


class TestMembershipIsOneRow:
    """The writes behind「加入」, and what makes repeating them safe.

    One live row per (space, person) is a property the database has to hold,
    not one a reader can establish: two requests for the same person really do
    arrive together — a double-tapped「加入」, or a join through the link while
    an admin adds that same person — and a read-then-write pair lets both read
    "not a member" and both write. That leaves two live rows for the pair,
    which turns `get_member`'s
    `scalar_one_or_none` into `MultipleResultsFound`: the documented
    double-tap no-op becoming a 500 on every later check of that membership.
    """

    def test_joining_twice_keeps_one_row(
        self, user_client: UserCreator, api_client: TestClient
    ):
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)

        joiner = user_client.create_user()
        joiner_token = _login(user_client, api_client, joiner)

        # The double tap. The second join is a no-op.
        _join(api_client, joiner_token, link)
        _join(api_client, joiner_token, link)

        assert [
            member["userId"] for member in _members(api_client, creator_token, space_id)
        ] == [joiner.user_id]
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
        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)

        joiner = user_client.create_user()
        joiner_token = _login(user_client, api_client, joiner)
        _join(api_client, joiner_token, link)
        assert (
            api_client.post(
                f"/spaces/{space_id}/leave", headers=_auth(joiner_token)
            ).status_code
            == 204
        )

        _join(api_client, joiner_token, link)

        assert [
            member["userId"] for member in _members(api_client, creator_token, space_id)
        ] == [joiner.user_id]
        # The table keeps one row for the pair rather than one per attempt.
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
        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)

        joiner = user_client.create_user()
        _join(api_client, _login(user_client, api_client, joiner), link)

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
        back, not the person joining through the link — and has to land on the same
        revive path.
        """
        creator = user_client.create_user()
        creator_token = _login(user_client, api_client, creator)
        created = _create_space(api_client, creator_token)
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
        created = _create_space(api_client, creator_token)
        space_id = created["space"]["id"]
        link = _link(api_client, creator_token, space_id)

        first = user_client.create_user()
        _join(api_client, _login(user_client, api_client, first), link)
        # Warm-up, so the measured calls are not the ones paying for anything
        # the process does once.
        api_client.get(f"/spaces/{space_id}/members", headers=_auth(creator_token))
        sql_log.clear()
        api_client.get(f"/spaces/{space_id}/members", headers=_auth(creator_token))
        one_member = _selects(sql_log)

        for _ in range(4):
            extra = user_client.create_user()
            _join(api_client, _login(user_client, api_client, extra), link)
        sql_log.clear()
        api_client.get(f"/spaces/{space_id}/members", headers=_auth(creator_token))
        five_members = _selects(sql_log)

        assert len(_members(api_client, creator_token, space_id)) == 5
        assert five_members == one_member, (
            f"{one_member} selects for 1 member but {five_members} for 5 — the "
            "hydration is per row again"
        )
