"""Space visibility tiers, membership and invite codes, end to end.

Walks the acceptance list for the 空间成员与邀请码 task:

- a 凭码 space is invisible in the list and through a direct link until the
  code is redeemed, and shows up on the redeemer's list right after;
- leaving hides it again and leaves the leaver's projects alone;
- a manager whose role was revoked is refused when removing a member;
- a 私人 space refuses non-members on both /spaces/{id} and /spaces/{id}/managers;
- 公开 spaces answer exactly as they did before (regression).
"""

import time

from fastapi.testclient import TestClient

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
