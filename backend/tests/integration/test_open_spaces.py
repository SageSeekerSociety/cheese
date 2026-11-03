"""An open space is read, and taken part in, by everyone in its email scope.

Product decision (知是可见性与加入规则, 2026-11): a space is members-only by
default. Its owner may make it open to a set of registered-email suffixes,
which the platform reviews first; until then the space stays as it was. In an
open space anyone signed in whose email is in the scope finds it in their
list, reads what a member reads, claims and publishes problems — all without
joining. Someone outside the scope sees nothing (404), unless an admin names
them. Going back to members-only needs no review.
"""

from __future__ import annotations

import time
import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    session_auth_headers,
    unique_int,
)

REVIEWER = "open-space-reviewer"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def reviewer(monkeypatch) -> dict[str, str]:
    monkeypatch.setattr(settings, "platform_admin_handles", [REVIEWER])
    return session_auth_headers(REVIEWER)


def _person(
    api_client: TestClient, user_client: UserCreator, domain: str
) -> tuple[int, str]:
    user = user_client.create_user(email=f"p-{uuid.uuid4().hex[:12]}@{domain}")
    return user.user_id, user_client.login(api_client, user.username, user.password)


def _space(api_client: TestClient, user_client: UserCreator) -> dict:
    """An approved members-only space with one approved problem."""
    _, owner = _person(api_client, user_client, "owner.example.org")
    word = f"harbor{unique_int(10000000, 99999999)}"
    resp = create_approved_space(
        api_client,
        json={
            "name": f"Open space ({word})",
            "intro": "全院的题目",
            "description": "一个题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=_auth(owner),
    )
    assert resp.status_code == 201, resp.text
    space_id = resp.json()["data"]["space"]["id"]
    task_id = _publish(api_client, owner, space_id, word)
    resp = api_client.patch(
        f"/tasks/{task_id}", json={"approved": "APPROVED"}, headers=_auth(owner)
    )
    assert resp.status_code == 200, resp.text
    return {"owner": owner, "space_id": space_id, "task_id": task_id}


def _publish(api_client: TestClient, token: str, space_id: int, word: str) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "name": f"{word} 实验",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": space_id,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _set_access(
    api_client: TestClient, space: dict, mode: str, scope: list[str] | None = None
) -> dict:
    resp = api_client.put(
        f"/spaces/{space['space_id']}/access",
        json={"mode": mode, "scope": scope or []},
        headers=_auth(space["owner"]),
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["space"]


def _queued_open_scope(
    api_client: TestClient, reviewer: dict, space: dict
) -> list[str] | None:
    """The scope the reviewers' queue shows this space asking to open to."""
    resp = api_client.get(
        "/admin/spaces", params={"status": "PENDING"}, headers=reviewer
    )
    assert resp.status_code == 200, resp.text
    return next(
        (
            item["openScope"]
            for item in resp.json()["data"]["items"]
            if item["kind"] == "open" and item["id"] == space["space_id"]
        ),
        None,
    )


def _review(
    api_client: TestClient,
    reviewer: dict,
    space: dict,
    approved: bool,
    reason: str = "",
    scope: list[str] | None = None,
):
    """A platform admin's decision, naming the scope the queue showed them, as
    the review page does."""
    return api_client.post(
        f"/admin/spaces/{space['space_id']}/review",
        json={"approved": approved, "reason": reason, "openScope": scope},
        headers=reviewer,
    )


def _open(api_client: TestClient, reviewer: dict, space: dict, scope: list[str]):
    _set_access(api_client, space, "open", scope)
    shown = _queued_open_scope(api_client, reviewer, space)
    resp = _review(api_client, reviewer, space, True, scope=shown)
    assert resp.status_code == 200, resp.text


def _listed(api_client: TestClient, token: str) -> set[int]:
    resp = api_client.get("/spaces", params={"pageSize": 200}, headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return {s["id"] for s in resp.json()["data"]["spaces"]}


def _reads(api_client: TestClient, token: str, space: dict) -> dict[str, int]:
    """What answers someone gets on the space's pages a member reads."""
    sid = space["space_id"]
    paths = {
        "space": f"/spaces/{sid}",
        "categories": f"/spaces/{sid}/categories",
        "announcements": f"/spaces/{sid}/announcements",
        "task": f"/tasks/{space['task_id']}",
    }
    return {
        name: api_client.get(path, headers=_auth(token)).status_code
        for name, path in paths.items()
    }


def _task_ids(api_client: TestClient, token: str, space: dict) -> set[int]:
    resp = api_client.get(
        "/tasks", params={"space": space["space_id"]}, headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    return {t["id"] for t in resp.json()["data"]["tasks"]}


def _member_ids(api_client: TestClient, space: dict) -> set[int]:
    resp = api_client.get(
        f"/spaces/{space['space_id']}/members", headers=_auth(space["owner"])
    )
    assert resp.status_code == 200, resp.text
    return {m["userId"] for m in resp.json()["data"]["members"]}


EVERY_PAGE_READS = {"space": 200, "categories": 200, "announcements": 200, "task": 200}
EVERY_PAGE_HIDDEN = {"space": 404, "categories": 404, "announcements": 404, "task": 404}


def test_someone_in_scope_reads_and_claims_in_an_open_space_without_joining(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    student_id, student = _person(api_client, user_client, "ruc.edu.cn")
    _, subdomain = _person(api_client, user_client, "mails.ruc.edu.cn")
    _open(api_client, reviewer, space, ["ruc.edu.cn"])

    for token in (student, subdomain):
        assert space["space_id"] in _listed(api_client, token)
        assert _reads(api_client, token, space) == EVERY_PAGE_READS
        assert _task_ids(api_client, token, space) == {space["task_id"]}

    claimed = api_client.post(
        f"/tasks/{space['task_id']}/participants", json={}, headers=_auth(student)
    )
    assert claimed.status_code in (200, 201), claimed.text
    published = _publish(api_client, student, space["space_id"], "student")
    assert published > 0
    # Reading, claiming and publishing wrote no membership.
    assert student_id not in _member_ids(api_client, space)


def test_someone_outside_the_scope_gets_404_in_an_open_space(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    _, outsider = _person(api_client, user_client, "example.com")
    # A domain that merely ends in the same letters is not inside the scope.
    _, lookalike = _person(api_client, user_client, "notruc.edu.cn")
    _open(api_client, reviewer, space, ["ruc.edu.cn"])

    for token in (outsider, lookalike):
        assert space["space_id"] not in _listed(api_client, token)
        assert _reads(api_client, token, space) == EVERY_PAGE_HIDDEN
        refused = api_client.post(
            f"/tasks/{space['task_id']}/participants", json={}, headers=_auth(token)
        )
        assert refused.status_code == 404, refused.text


def test_approval_applies_only_the_scope_the_reviewer_was_shown(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    _, stranger = _person(api_client, user_client, "gmail.com")
    _set_access(api_client, space, "open", ["ruc.edu.cn"])
    shown = _queued_open_scope(api_client, reviewer, space)
    # The owner rewrites the waiting request after the reviewer looked at it.
    _set_access(api_client, space, "open", ["gmail.com"])

    stale = _review(api_client, reviewer, space, True, scope=shown)
    assert stale.status_code == 409, stale.text
    assert _reads(api_client, stranger, space) == EVERY_PAGE_HIDDEN
    seen = api_client.get(f"/spaces/{space['space_id']}", headers=_auth(space["owner"]))
    assert seen.json()["data"]["space"]["openScope"] is None

    # A decision that names nothing it was shown decides nothing.
    blind = _review(api_client, reviewer, space, True)
    assert blind.status_code == 409, blind.text
    assert _reads(api_client, stranger, space) == EVERY_PAGE_HIDDEN


def test_an_open_space_shows_its_member_list_only_to_its_admins(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    _, student = _person(api_client, user_client, "ruc.edu.cn")
    _open(api_client, reviewer, space, ["ruc.edu.cn"])

    roster = f"/spaces/{space['space_id']}/members"
    assert api_client.get(roster, headers=_auth(student)).status_code == 403
    assert api_client.get(roster, headers=_auth(space["owner"])).status_code == 200


def test_an_outsider_an_admin_names_reads_the_open_space(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    outsider_id, outsider = _person(api_client, user_client, "partner.example.com")
    _open(api_client, reviewer, space, ["ruc.edu.cn"])
    assert _reads(api_client, outsider, space) == EVERY_PAGE_HIDDEN

    resp = api_client.post(
        f"/spaces/{space['space_id']}/members",
        json={"userId": outsider_id},
        headers=_auth(space["owner"]),
    )
    assert resp.status_code == 201, resp.text
    assert space["space_id"] in _listed(api_client, outsider)
    assert _reads(api_client, outsider, space) == EVERY_PAGE_READS


def test_a_members_only_space_stays_closed_to_everyone_in_the_suffix(
    api_client: TestClient, user_client: UserCreator
):
    space = _space(api_client, user_client)
    _, student = _person(api_client, user_client, "ruc.edu.cn")

    assert space["space_id"] not in _listed(api_client, student)
    assert _reads(api_client, student, space) == EVERY_PAGE_HIDDEN


def test_opening_a_space_waits_for_the_platform_review(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    _, student = _person(api_client, user_client, "ruc.edu.cn")

    shown = _set_access(api_client, space, "open", ["RUC.edu.cn"])
    assert shown["openScope"] is None
    assert shown["openRequest"]["status"] == "PENDING"
    assert shown["openRequest"]["scope"] == ["ruc.edu.cn"]
    assert _reads(api_client, student, space) == EVERY_PAGE_HIDDEN

    assert _queued_open_scope(api_client, reviewer, space) == ["ruc.edu.cn"]
    shown = ["ruc.edu.cn"]
    owner_tries = _review(api_client, _auth(space["owner"]), space, True, scope=shown)
    assert owner_tries.status_code == 403
    no_reason = _review(api_client, reviewer, space, False, " ", scope=shown)
    assert no_reason.status_code == 400
    rejected = _review(
        api_client, reviewer, space, False, "说明这个空间给谁用", scope=shown
    )
    assert rejected.status_code == 200, rejected.text
    assert _reads(api_client, student, space) == EVERY_PAGE_HIDDEN
    seen = api_client.get(f"/spaces/{space['space_id']}", headers=_auth(space["owner"]))
    assert seen.json()["data"]["space"]["openRequest"]["status"] == "REJECTED"
    assert seen.json()["data"]["space"]["openRequest"]["reason"] == "说明这个空间给谁用"
    assert _review(api_client, reviewer, space, True, scope=shown).status_code == 409

    _set_access(api_client, space, "open", ["ruc.edu.cn"])
    approved = _review(api_client, reviewer, space, True, scope=shown)
    assert approved.status_code == 200, approved.text
    assert _reads(api_client, student, space) == EVERY_PAGE_READS


def test_widening_an_open_space_waits_for_review_and_narrowing_does_not(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    _, student = _person(api_client, user_client, "ruc.edu.cn")
    _, other = _person(api_client, user_client, "pku.edu.cn")
    _open(api_client, reviewer, space, ["ruc.edu.cn"])

    widened = _set_access(api_client, space, "open", ["ruc.edu.cn", "pku.edu.cn"])
    assert widened["openScope"] == ["ruc.edu.cn"]
    assert _reads(api_client, other, space) == EVERY_PAGE_HIDDEN

    narrowed = _set_access(api_client, space, "open", ["mails.ruc.edu.cn"])
    assert narrowed["openScope"] == ["mails.ruc.edu.cn"]
    assert _reads(api_client, student, space) == EVERY_PAGE_HIDDEN

    closed = _set_access(api_client, space, "members")
    assert closed["openScope"] is None
    assert _reads(api_client, student, space) == EVERY_PAGE_HIDDEN


def test_a_problem_narrowed_to_other_domains_stays_hidden_in_an_open_space(
    api_client: TestClient, user_client: UserCreator, reviewer: dict
):
    space = _space(api_client, user_client)
    _, student = _person(api_client, user_client, "ruc.edu.cn")
    _open(api_client, reviewer, space, ["ruc.edu.cn"])
    group = api_client.post(
        f"/spaces/{space['space_id']}/domain-groups",
        json={"name": "合作方", "domains": ["partner.example.com"]},
        headers=_auth(space["owner"]),
    )
    assert group.status_code in (200, 201), group.text
    group_id = group.json()["data"]["group"]["id"]
    resp = api_client.patch(
        f"/tasks/{space['task_id']}",
        json={"accessControlEnabled": True, "accessDomainGroupIds": [group_id]},
        headers=_auth(space["owner"]),
    )
    assert resp.status_code == 200, resp.text

    assert (
        api_client.get(f"/tasks/{space['task_id']}", headers=_auth(student)).status_code
        == 404
    )
    assert _task_ids(api_client, student, space) == set()


@pytest.mark.parametrize("scope", [[], ["%.cn"], ["not a domain"], ["cn"]])
def test_an_open_space_needs_a_valid_suffix(
    api_client: TestClient, user_client: UserCreator, scope: list[str]
):
    space = _space(api_client, user_client)
    resp = api_client.put(
        f"/spaces/{space['space_id']}/access",
        json={"mode": "open", "scope": scope},
        headers=_auth(space["owner"]),
    )
    assert resp.status_code == 400, resp.text
