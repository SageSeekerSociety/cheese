"""A platform admin manages every space and every shared team without a seat in
it — and reads nothing in them that is someone's own.

Managing is the people, the settings, approving 题目, archiving and deleting.
What a team's members keep for themselves — its documents, its machines and
model connections — stays theirs; a personal team stays its owner's alone.
Someone who is not a platform admin and has no seat is answered as before.
"""

import pytest

from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import create_approved_space
from tests.integration.test_team_membership import team_of

OVERSEER = "pa-overseer"
OWNER, MEMBER, OUTSIDER = "pa-owner", "pa-member", "pa-outsider"
T_OWNER, T_ADMIN, T_MEMBER = "pa-t-owner", "pa-t-admin", "pa-t-member"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def people(client, monkeypatch) -> dict[str, dict[str, str]]:
    handles = (OVERSEER, OWNER, MEMBER, OUTSIDER, T_OWNER, T_ADMIN, T_MEMBER)
    tokens = {handle: _auth(seed_user(client, handle)) for handle in handles}
    monkeypatch.setattr(settings, "platform_admin_handles", [OVERSEER])
    return tokens


def _id_of(client, people, handle: str) -> int:
    found = client.get("/users/lookup", params={"q": handle}, headers=people[OWNER])
    assert found.status_code == 200, found.text
    return found.json()["data"]["id"]


def _space(client, people) -> int:
    """A members-only space the overseer holds no seat in."""
    made = create_approved_space(
        client,
        json={"name": "一门课", "intro": "每周练习", "avatarId": 1},
        headers=people[OWNER],
    )
    assert made.status_code == 201, made.text
    return made.json()["data"]["space"]["id"]


def _members(client, people, space_id: int) -> set[int]:
    listed = client.get(f"/spaces/{space_id}/members", headers=people[OWNER])
    assert listed.status_code == 200, listed.text
    return {row["user"]["id"] for row in listed.json()["data"]["members"]}


def _try_publish(client, headers, space_id: int):
    return client.post(
        "/tasks",
        json={
            "name": "等审的题",
            "space": space_id,
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
        },
        headers=headers,
    )


def _publish(client, headers, space_id: int) -> int:
    made = _try_publish(client, headers, space_id)
    assert made.status_code == 200, made.text
    return made.json()["data"]["task"]["id"]


# -- spaces ---------------------------------------------------------------------


def test_a_platform_admin_opens_a_members_only_space_and_is_told_they_manage_it(
    client, people
):
    space_id = _space(client, people)

    opened = client.get(f"/spaces/{space_id}", headers=people[OVERSEER])

    assert opened.status_code == 200, opened.text
    held = set(opened.json()["data"]["space"]["viewerPermissions"])
    assert {"edit_settings", "manage_members", "manage_admins", "delete"} <= held
    assert "publish" not in held


def test_a_member_is_told_they_only_view_and_publish(client, people):
    space_id = _space(client, people)
    member_id = _id_of(client, people, MEMBER)
    added = client.post(
        f"/spaces/{space_id}/members",
        json={"userId": member_id},
        headers=people[OWNER],
    )
    assert added.status_code == 201, added.text

    opened = client.get(f"/spaces/{space_id}", headers=people[MEMBER])

    assert opened.status_code == 200, opened.text
    held = set(opened.json()["data"]["space"]["viewerPermissions"])
    assert held == {"view", "publish"}


def test_someone_with_no_seat_who_is_not_an_admin_still_finds_no_space(client, people):
    space_id = _space(client, people)

    assert (
        client.get(f"/spaces/{space_id}", headers=people[OUTSIDER]).status_code == 404
    )
    refused = client.patch(
        f"/spaces/{space_id}", json={"intro": "改了"}, headers=people[OUTSIDER]
    )
    assert refused.status_code == 404, refused.text


def test_a_platform_admin_edits_the_settings_and_the_roster(client, people):
    space_id = _space(client, people)
    member_id = _id_of(client, people, MEMBER)

    edited = client.patch(
        f"/spaces/{space_id}", json={"intro": "平台改的"}, headers=people[OVERSEER]
    )
    assert edited.status_code == 200, edited.text
    read = client.get(f"/spaces/{space_id}", headers=people[OWNER]).json()
    assert read["data"]["space"]["intro"] == "平台改的"

    added = client.post(
        f"/spaces/{space_id}/members",
        json={"userId": member_id},
        headers=people[OVERSEER],
    )
    assert added.status_code == 201, added.text
    assert member_id in _members(client, people, space_id)

    removed = client.delete(
        f"/spaces/{space_id}/members/{member_id}", headers=people[OVERSEER]
    )
    assert removed.status_code in (200, 204), removed.text
    assert member_id not in _members(client, people, space_id)


def test_a_platform_admin_approves_a_problem_from_the_space_itself(client, people):
    space_id = _space(client, people)
    task_id = _publish(client, people[OWNER], space_id)

    approved = client.patch(
        f"/tasks/{task_id}", json={"approved": "APPROVED"}, headers=people[OVERSEER]
    )

    assert approved.status_code == 200, approved.text
    task = client.get(f"/tasks/{task_id}", headers=people[OWNER]).json()
    assert task["data"]["task"]["approved"] == "APPROVED"


def test_a_platform_admin_does_not_publish_in_a_space_they_are_not_in(client, people):
    space_id = _space(client, people)

    refused = _try_publish(client, people[OVERSEER], space_id)

    assert refused.status_code in (403, 404), refused.text


def test_a_platform_admin_deletes_the_space(client, people):
    space_id = _space(client, people)

    deleted = client.delete(f"/spaces/{space_id}", headers=people[OVERSEER])

    assert deleted.status_code in (200, 204), deleted.text
    assert client.get(f"/spaces/{space_id}", headers=people[OWNER]).status_code == 404


# -- teams ----------------------------------------------------------------------


@pytest.fixture
def team(client, people) -> int:
    """A stealth team: nobody off it finds it by id."""
    team_id = team_of(client, owner=T_OWNER, admins=(T_ADMIN,), members=(T_MEMBER,))
    hidden = client.patch(
        f"/teams/{team_id}", json={"visibility": "stealth"}, headers=people[T_OWNER]
    )
    assert hidden.status_code == 200, hidden.text
    return team_id


def _roster(client, people, team_id: int) -> dict[str, str]:
    listed = client.get(f"/teams/{team_id}/members", headers=people[T_OWNER])
    assert listed.status_code == 200, listed.text
    return {m["user"]["username"]: m["role"] for m in listed.json()["data"]["members"]}


def test_a_platform_admin_opens_a_team_they_are_not_on(client, people, team):
    opened = client.get(f"/teams/{team}", headers=people[OVERSEER])

    assert opened.status_code == 200, opened.text
    held = set(opened.json()["data"]["team"]["viewerPermissions"])
    assert {"edit_settings", "manage_members", "disband", "transfer"} <= held
    assert {"manage_machines", "manage_documents"}.isdisjoint(held)
    assert client.get(f"/teams/{team}", headers=people[OUTSIDER]).status_code == 404


def test_a_platform_admin_changes_the_settings_and_takes_out_an_admin(
    client, people, team
):
    edited = client.patch(
        f"/teams/{team}", json={"intro": "平台改的"}, headers=people[OVERSEER]
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["data"]["team"]["intro"] == "平台改的"

    listed = client.get(f"/teams/{team}/members", headers=people[OVERSEER])
    assert listed.status_code == 200, listed.text
    removable = {
        m["user"]["username"]
        for m in listed.json()["data"]["members"]
        if m["canRemove"]
    }
    assert removable == {T_ADMIN, T_MEMBER}

    admin_id = _id_of(client, people, T_ADMIN)
    removed = client.delete(
        f"/teams/{team}/members/{admin_id}", headers=people[OVERSEER]
    )
    assert removed.status_code == 204, removed.text
    assert T_ADMIN not in _roster(client, people, team)

    owner_id = _id_of(client, people, T_OWNER)
    refused = client.delete(
        f"/teams/{team}/members/{owner_id}", headers=people[OVERSEER]
    )
    assert refused.status_code == 403, refused.text
    assert _roster(client, people, team)[T_OWNER] == "OWNER"


def test_a_platform_admin_hands_the_team_over_and_disbands_it(client, people, team):
    member_id = _id_of(client, people, T_MEMBER)

    handed = client.put(
        f"/teams/{team}/owner", json={"userId": member_id}, headers=people[OVERSEER]
    )
    assert handed.status_code == 200, handed.text
    assert _roster(client, people, team)[T_MEMBER] == "OWNER"

    disbanded = client.delete(f"/teams/{team}", headers=people[OVERSEER])
    assert disbanded.status_code == 204, disbanded.text
    assert client.get(f"/teams/{team}", headers=people[T_MEMBER]).status_code == 404


def test_a_platform_admin_reads_neither_a_teams_documents_nor_its_machines(
    client, people, team
):
    for path in (f"/teams/{team}/documents", f"/teams/{team}/model-connections"):
        as_outsider = client.get(path, headers=people[OUTSIDER])
        as_overseer = client.get(path, headers=people[OVERSEER])
        assert as_outsider.status_code in (403, 404), as_outsider.text
        assert as_overseer.status_code == as_outsider.status_code, as_overseer.text


def test_a_personal_team_stays_its_owners_alone(client, people):
    mine = client.get("/teams/my-teams", headers=people[T_MEMBER])
    assert mine.status_code == 200, mine.text
    personal = next(t for t in mine.json()["data"]["teams"] if t["personal"])

    assert (
        client.get(f"/teams/{personal['id']}", headers=people[OVERSEER]).status_code
        == 404
    )
    refused = client.patch(
        f"/teams/{personal['id']}", json={"intro": "改了"}, headers=people[OVERSEER]
    )
    assert refused.status_code == 404, refused.text


# -- oversight never seats the overseer -----------------------------------------
#
# A seat reads what a place's people keep to themselves (a team's projects and
# documents, a space's members-only board). Oversight manages a place for its
# people; it never puts the overseer in it, nor hands them a way in.


def _refused_as_own_seat(resp) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "overseerNoOwnSeat", resp.text


def _refused_as_way_in(resp) -> None:
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["i18n"]["key"] == "overseerNoWayIn", resp.text


def test_a_platform_admin_does_not_add_invite_promote_or_hand_a_team_to_themselves(
    client, people, team
):
    me = _id_of(client, people, OVERSEER)
    base = f"/teams/{team}"
    as_me = people[OVERSEER]

    _refused_as_own_seat(
        client.post(f"{base}/members", json={"userId": me}, headers=as_me)
    )
    _refused_as_own_seat(
        client.post(f"{base}/invitations", json={"userId": me}, headers=as_me)
    )
    _refused_as_own_seat(
        client.patch(f"{base}/members/{me}", json={"role": "ADMIN"}, headers=as_me)
    )
    _refused_as_own_seat(
        client.put(f"{base}/owner", json={"userId": me}, headers=as_me)
    )
    assert OVERSEER not in _roster(client, people, team)


def test_a_platform_admin_still_seats_and_moves_other_people_on_a_team(
    client, people, team
):
    outsider = _id_of(client, people, OUTSIDER)
    member = _id_of(client, people, T_MEMBER)

    added = client.post(
        f"/teams/{team}/members",
        json={"userId": outsider},
        headers=people[OVERSEER],
    )
    assert added.status_code == 201, added.text
    promoted = client.patch(
        f"/teams/{team}/members/{member}",
        json={"role": "ADMIN"},
        headers=people[OVERSEER],
    )
    assert promoted.status_code == 200, promoted.text
    roster = _roster(client, people, team)
    assert (roster[OUTSIDER], roster[T_MEMBER]) == ("MEMBER", "ADMIN")


def test_a_platform_admin_gets_no_team_join_link(client, people, team):
    base = f"/teams/{team}/join-link"

    _refused_as_way_in(client.get(base, headers=people[OVERSEER]))
    _refused_as_way_in(client.post(f"{base}/reset", headers=people[OVERSEER]))
    _refused_as_way_in(
        client.patch(base, json={"approval": False}, headers=people[OVERSEER])
    )
    assert client.get(base, headers=people[T_OWNER]).status_code == 200


def test_a_platform_admin_cannot_ask_to_join_a_stealth_team_they_only_oversee(
    client, people, team
):
    asked = client.post(f"/teams/{team}/join", headers=people[OVERSEER])

    assert asked.status_code == 404, asked.text
    assert OVERSEER not in _roster(client, people, team)


def test_a_platform_admin_does_not_answer_their_own_request_to_join(client, people):
    team_id = team_of(client, owner=T_OWNER, admins=(T_ADMIN,), members=(T_MEMBER,))
    asking = client.patch(
        f"/teams/{team_id}/join-link", json={"approval": True}, headers=people[T_OWNER]
    )
    assert asking.status_code == 200, asking.text
    asked = client.post(f"/teams/{team_id}/join", headers=people[OVERSEER])
    assert asked.json()["data"]["team"]["joinStatus"] == "pending", asked.text
    waiting = client.get(
        f"/teams/{team_id}/requests",
        params={"status": "PENDING"},
        headers=people[T_OWNER],
    ).json()["data"]["applications"]
    (request_id,) = [a["id"] for a in waiting]

    _refused_as_own_seat(
        client.post(
            f"/teams/{team_id}/join-requests/{request_id}/approve",
            headers=people[OVERSEER],
        )
    )
    assert OVERSEER not in _roster(client, people, team_id)


def test_a_platform_admin_does_not_seat_themselves_in_a_space(client, people):
    space_id = _space(client, people)
    me = _id_of(client, people, OVERSEER)
    base = f"/spaces/{space_id}"
    as_me = people[OVERSEER]

    _refused_as_own_seat(
        client.post(f"{base}/members", json={"userId": me}, headers=as_me)
    )
    _refused_as_own_seat(
        client.post(
            f"{base}/managers", json={"userId": me, "role": "ADMIN"}, headers=as_me
        )
    )
    _refused_as_own_seat(
        client.patch(f"{base}/managers/{me}", json={"role": "OWNER"}, headers=as_me)
    )
    assert me not in _members(client, people, space_id)
    held = client.get(base, headers=as_me).json()["data"]["space"]
    assert me not in {a["user"]["id"] for a in held["admins"]}


def test_a_platform_admin_gets_no_space_join_link(client, people):
    space_id = _space(client, people)
    base = f"/spaces/{space_id}/join-link"

    _refused_as_way_in(client.get(base, headers=people[OVERSEER]))
    _refused_as_way_in(client.post(f"{base}/reset", headers=people[OVERSEER]))
    assert client.get(base, headers=people[OWNER]).status_code == 200


def test_a_platform_admin_does_not_answer_their_own_request_to_join_a_space(
    client, people
):
    space_id = _space(client, people)
    link = client.patch(
        f"/spaces/{space_id}/join-link",
        json={"approval": True},
        headers=people[OWNER],
    )
    assert link.status_code == 200, link.text
    token = link.json()["data"]["token"]
    asked = client.post(f"/space-invites/{token}/join", headers=people[OVERSEER])
    assert asked.status_code == 200, asked.text
    waiting = client.get(
        f"/spaces/{space_id}/join-requests", headers=people[OWNER]
    ).json()["data"]["requests"]
    (request_id,) = [r["id"] for r in waiting]

    _refused_as_own_seat(
        client.post(
            f"/spaces/{space_id}/join-requests/{request_id}/approve",
            headers=people[OVERSEER],
        )
    )
    still = client.get(f"/spaces/{space_id}/join-requests", headers=people[OWNER])
    assert [r["id"] for r in still.json()["data"]["requests"]] == [request_id]
    assert _id_of(client, people, OVERSEER) not in _members(client, people, space_id)
