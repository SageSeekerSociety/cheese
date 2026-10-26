"""Who may and who may not act on a problem (题目) published in a space, over HTTP.

Each test states one rule and checks it at the lowest standing that holds it,
the standing just below, and (where it reads differently) a signed-in person
outside the space. The cast of every test is the same space:

- ``owner``: made the space (its OWNER);
- ``admin``: a space manager who did not write the problem;
- ``publisher``: a plain member who wrote the problem (its creator);
- ``participant``: a member whose claim on it is approved and who handed in;
- ``member``: a member with no claim;
- ``outsider``: signed in, not in the space.

Cells other test files already pin (an outsider editing or deleting, a member
reading the roster or someone's submissions, a participant grading, the
creator approving their own problem, ...) are not repeated here.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import (
    UserCreator,
    create_approved_space,
    unique_int,
)


@dataclass
class Person:
    user_id: int
    headers: dict[str, str]


@dataclass
class Space:
    space_id: int
    category_id: int | None
    owner: Person
    admin: Person
    publisher: Person
    participant: Person
    member: Person
    outsider: Person
    task_id: int
    claim_id: int
    submission_id: int


def _person(api_client: TestClient, user_client: UserCreator) -> Person:
    user = user_client.create_user()
    token = user_client.login(api_client, user.username, user.password)
    return Person(user.user_id, {"Authorization": f"Bearer {token}"})


def _publish(api_client: TestClient, space: Space, by: Person) -> int:
    resp = api_client.post(
        "/tasks",
        json={
            "mentors": [],
            "name": f"题{unique_int()}",
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "space": space.space_id,
            "categoryId": space.category_id,
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
        },
        headers=by.headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["task"]["id"]


def _claim(api_client: TestClient, task_id: int, by: Person) -> int:
    resp = api_client.post(
        f"/tasks/{task_id}/participants",
        params={"member": by.user_id},
        json={},
        headers=by.headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["participant"]["id"]


def _decide_claim(
    api_client: TestClient, task_id: int, claim_id: int, by: Person, verdict: str
):
    return api_client.patch(
        f"/tasks/{task_id}/participants/{claim_id}",
        json={"approved": verdict},
        headers=by.headers,
    )


def _roster(api_client: TestClient, space: Space, task_id: int) -> dict[int, dict]:
    resp = api_client.get(f"/tasks/{task_id}/participants", headers=space.owner.headers)
    assert resp.status_code == 200, resp.text
    return {p["id"]: p for p in resp.json()["data"]["participants"]}


@pytest.fixture
def space(api_client: TestClient, user_client: UserCreator) -> Space:
    owner = _person(api_client, user_client)
    made = create_approved_space(
        api_client,
        json={
            "name": f"课 ({unique_int()})",
            "intro": "一门课",
            "description": "题目板",
            "avatarId": 1,
            "taskTemplates": [],
        },
        headers=owner.headers,
    )
    assert made.status_code == 201, made.text
    row = made.json()["data"]["space"]
    space_id = row["id"]

    admin, publisher, participant, member, outsider = (
        _person(api_client, user_client) for _ in range(5)
    )
    granted = api_client.post(
        f"/spaces/{space_id}/managers",
        json={"userId": admin.user_id, "role": "ADMIN"},
        headers=owner.headers,
    )
    assert granted.status_code == 201, granted.text
    for person in (publisher, participant, member):
        joined = api_client.post(
            f"/spaces/{space_id}/members",
            json={"userId": person.user_id},
            headers=owner.headers,
        )
        assert joined.status_code == 201, joined.text

    world = Space(
        space_id=space_id,
        category_id=row.get("defaultCategoryId"),
        owner=owner,
        admin=admin,
        publisher=publisher,
        participant=participant,
        member=member,
        outsider=outsider,
        task_id=0,
        claim_id=0,
        submission_id=0,
    )
    world.task_id = _publish(api_client, world, publisher)
    approved = api_client.patch(
        f"/tasks/{world.task_id}",
        json={"approved": "APPROVED"},
        headers=owner.headers,
    )
    assert approved.status_code == 200, approved.text

    world.claim_id = _claim(api_client, world.task_id, participant)
    decided = _decide_claim(
        api_client, world.task_id, world.claim_id, owner, "APPROVED"
    )
    assert decided.status_code == 200, decided.text
    handed_in = api_client.post(
        f"/tasks/{world.task_id}/participants/{world.claim_id}/submissions",
        json=[{"text": "这是提交。"}],
        headers=participant.headers,
    )
    assert handed_in.status_code == 200, handed_in.text
    world.submission_id = handed_in.json()["data"]["submission"]["id"]
    return world


# --- the problem itself --------------------------------------------------------


def test_a_space_manager_edits_a_problem_someone_else_wrote_and_a_member_cannot(
    api_client: TestClient, space: Space
):
    """Only the problem's creator or a manager of its space can edit it."""
    path = f"/tasks/{space.task_id}"
    for who in (space.member, space.participant):
        refused = api_client.patch(path, json={"name": "改不动"}, headers=who.headers)
        assert refused.status_code == 403, refused.text

    edited = api_client.patch(
        path, json={"name": "管理员改的名"}, headers=space.admin.headers
    )
    assert edited.status_code == 200, edited.text
    by_creator = api_client.patch(
        path, json={"intro": "出题人改的简介"}, headers=space.publisher.headers
    )
    assert by_creator.status_code == 200, by_creator.text

    task = api_client.get(path, headers=space.owner.headers).json()["data"]["task"]
    assert (task["name"], task["intro"]) == ("管理员改的名", "出题人改的简介")


def test_a_space_manager_approves_a_problem_someone_else_wrote(
    api_client: TestClient, space: Space
):
    """A manager of the space approves a problem that a member published."""
    pending = _publish(api_client, space, space.member)

    approved = api_client.patch(
        f"/tasks/{pending}",
        json={"approved": "APPROVED"},
        headers=space.admin.headers,
    )
    assert approved.status_code == 200, approved.text
    task = api_client.get(f"/tasks/{pending}", headers=space.member.headers)
    assert task.json()["data"]["task"]["approved"] == "APPROVED"


def test_a_space_manager_deletes_a_problem_someone_else_wrote_and_a_member_cannot(
    api_client: TestClient, space: Space
):
    """Only the problem's creator or a manager of its space can delete it."""
    path = f"/tasks/{space.task_id}"
    refused = api_client.delete(path, headers=space.member.headers)
    assert refused.status_code == 403, refused.text

    deleted = api_client.delete(path, headers=space.admin.headers)
    assert deleted.status_code == 204, deleted.text
    assert api_client.get(path, headers=space.owner.headers).status_code == 404


def test_a_space_member_may_not_open_an_edit_draft_of_someone_elses_problem(
    api_client: TestClient, space: Space
):
    """Opening the edit draft of a published problem is for whoever may edit it."""
    refused = api_client.post(
        "/task-drafts", json={"task": space.task_id}, headers=space.member.headers
    )
    assert refused.status_code == 403, refused.text


# --- claims and the roster -----------------------------------------------------


def test_only_the_creator_or_a_space_manager_reads_the_roster(
    api_client: TestClient, space: Space
):
    """A participant does not read the roster of the problem they claimed; the
    problem's creator does, even when not a manager of the space."""
    path = f"/tasks/{space.task_id}/participants"
    refused = api_client.get(path, headers=space.participant.headers)
    assert refused.status_code == 403, refused.text

    allowed = api_client.get(path, headers=space.publisher.headers)
    assert allowed.status_code == 200, allowed.text
    ids = {p["id"] for p in allowed.json()["data"]["participants"]}
    assert space.claim_id in ids


def test_a_claimant_cannot_decide_on_their_own_claim_and_the_creator_can(
    api_client: TestClient, space: Space
):
    """Only the problem's creator or a space manager decides on a claim, by its
    id: neither the claimant nor another member can."""
    claim = _claim(api_client, space.task_id, space.member)
    before = _roster(api_client, space, space.task_id)[claim]["approved"]

    for who in (space.member, space.participant):
        refused = _decide_claim(api_client, space.task_id, claim, who, "DISAPPROVED")
        assert refused.status_code == 403, refused.text
    assert _roster(api_client, space, space.task_id)[claim]["approved"] == before

    allowed = _decide_claim(
        api_client, space.task_id, claim, space.publisher, "DISAPPROVED"
    )
    assert allowed.status_code == 200, allowed.text
    assert _roster(api_client, space, space.task_id)[claim]["approved"] == (
        "DISAPPROVED"
    )


def test_a_claimant_cannot_decide_on_their_own_claim_by_member_id(
    api_client: TestClient, space: Space
):
    """Only the problem's creator or a space manager decides on a claim, by member."""
    claim = _claim(api_client, space.task_id, space.member)
    before = _roster(api_client, space, space.task_id)[claim]["approved"]
    path = f"/tasks/{space.task_id}/participants"

    refused = api_client.patch(
        path,
        params={"member": space.member.user_id},
        json={"approved": "DISAPPROVED"},
        headers=space.member.headers,
    )
    assert refused.status_code == 403, refused.text
    assert _roster(api_client, space, space.task_id)[claim]["approved"] == before

    allowed = api_client.patch(
        path,
        params={"member": space.member.user_id},
        json={"approved": "DISAPPROVED"},
        headers=space.admin.headers,
    )
    assert allowed.status_code == 200, allowed.text
    assert _roster(api_client, space, space.task_id)[claim]["approved"] == (
        "DISAPPROVED"
    )


def test_a_claimant_withdraws_their_own_claim_but_not_someone_elses(
    api_client: TestClient, space: Space
):
    """A claim is withdrawn by its claimant, the problem's creator or a space
    manager; another participant cannot withdraw it."""
    theirs = _claim(api_client, space.task_id, space.member)
    path = f"/tasks/{space.task_id}/participants/{theirs}"

    refused = api_client.delete(path, headers=space.participant.headers)
    assert refused.status_code == 403, refused.text
    assert theirs in _roster(api_client, space, space.task_id)

    withdrawn = api_client.delete(path, headers=space.member.headers)
    assert withdrawn.status_code == 204, withdrawn.text
    assert theirs not in _roster(api_client, space, space.task_id)

    removed = api_client.delete(
        f"/tasks/{space.task_id}/participants/{space.claim_id}",
        headers=space.admin.headers,
    )
    assert removed.status_code == 204, removed.text
    assert space.claim_id not in _roster(api_client, space, space.task_id)


def test_a_claimant_withdraws_their_own_claim_by_member_id_but_not_someone_elses(
    api_client: TestClient, space: Space
):
    """Withdrawing by member id follows the same rule as withdrawing by claim id."""
    theirs = _claim(api_client, space.task_id, space.member)
    path = f"/tasks/{space.task_id}/participants"

    refused = api_client.delete(
        path, params={"member": space.member.user_id}, headers=space.participant.headers
    )
    assert refused.status_code == 403, refused.text
    assert theirs in _roster(api_client, space, space.task_id)

    withdrawn = api_client.delete(
        path, params={"member": space.member.user_id}, headers=space.member.headers
    )
    assert withdrawn.status_code == 204, withdrawn.text
    assert theirs not in _roster(api_client, space, space.task_id)

    removed = api_client.delete(
        path,
        params={"member": space.participant.user_id},
        headers=space.publisher.headers,
    )
    assert removed.status_code == 204, removed.text
    assert space.claim_id not in _roster(api_client, space, space.task_id)


def test_a_space_manager_enrols_someone_else_on_a_problem(
    api_client: TestClient, space: Space
):
    """A space manager may enrol another person on a problem they did not write."""
    enrolled = api_client.post(
        f"/tasks/{space.task_id}/participants",
        params={"member": space.member.user_id},
        json={},
        headers=space.admin.headers,
    )
    assert enrolled.status_code == 200, enrolled.text
    members = {
        p["memberId"] for p in _roster(api_client, space, space.task_id).values()
    }
    assert space.member.user_id in members


# --- submissions and reviews ---------------------------------------------------


def test_only_the_claimant_hands_in_work_on_a_claim(
    api_client: TestClient, space: Space
):
    """Work on a claim is handed in by the claimant alone: not the problem's
    creator, not a space manager, not another member."""
    path = f"/tasks/{space.task_id}/participants/{space.claim_id}/submissions"
    for who in (space.publisher, space.admin, space.member):
        refused = api_client.post(path, json=[{"text": "代交"}], headers=who.headers)
        assert refused.status_code == 403, refused.text

    versions = api_client.get(path, headers=space.owner.headers)
    assert versions.status_code == 200, versions.text


def test_a_claimant_reads_their_own_submissions_and_review_and_a_member_does_not(
    api_client: TestClient, space: Space
):
    """A claimant reads their own submissions and the review of them; another
    member of the space reads neither."""
    submissions = f"/tasks/{space.task_id}/participants/{space.claim_id}/submissions"
    review = f"{submissions}/{space.submission_id}/review"

    mine = api_client.get(submissions, headers=space.participant.headers)
    assert mine.status_code == 200, mine.text

    graded = api_client.post(
        review,
        json={"accepted": True, "score": 5, "comment": "好"},
        headers=space.publisher.headers,
    )
    assert graded.status_code == 200, graded.text

    read = api_client.get(review, headers=space.participant.headers)
    assert read.status_code == 200, read.text
    assert read.json()["data"]["review"]["detail"]["score"] == 5

    refused = api_client.get(review, headers=space.member.headers)
    assert refused.status_code == 403, refused.text


def test_only_whoever_teaches_a_problem_sees_how_all_its_claims_stand(
    api_client: TestClient, space: Space
):
    """The per-stage distribution of every claim is for the problem's creator and
    the space's managers; a participant sees only their own claim."""
    path = f"/tasks/{space.task_id}/progress"

    for who in (space.admin, space.publisher):
        teacher = api_client.get(path, headers=who.headers)
        assert teacher.status_code == 200, teacher.text
        assert teacher.json()["data"]["distribution"] is not None

    student = api_client.get(path, headers=space.participant.headers)
    assert student.status_code == 200, student.text
    assert student.json()["data"]["distribution"] is None
    assert student.json()["data"]["mine"]["participantId"] == space.claim_id


# --- the problem's files -------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "suffix", "body"),
    [
        ("POST", "/files/uploads", {"name": "a.txt", "size": 1}),
        ("DELETE", "/starter", None),
    ],
)
def test_a_participant_does_not_change_a_problems_files(
    api_client: TestClient, space: Space, method: str, suffix: str, body
):
    """Changing a problem's files is for its creator or a space manager, not the
    people working on it."""
    resp = api_client.request(
        method,
        f"/tasks/{space.task_id}{suffix}",
        json=body,
        headers=space.participant.headers,
    )
    assert resp.status_code == 403, resp.text


# --- signed out ----------------------------------------------------------------

_T = "/tasks/1"
_P = f"{_T}/participants/1"
_R = f"{_P}/submissions/1/review"
_REVIEW = {"accepted": True, "score": 1, "comment": "c"}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/tasks", {"name": "x", "space": 1}),
        ("GET", _T, None),
        ("PATCH", _T, {"name": "x"}),
        ("DELETE", _T, None),
        ("POST", f"{_T}/resubmit", None),
        ("GET", f"{_T}/participants", None),
        ("POST", f"{_T}/participants", {}),
        ("PATCH", f"{_T}/participants?member=1", {"approved": "APPROVED"}),
        ("DELETE", f"{_T}/participants?member=1", None),
        ("POST", f"{_T}/participations/user", {}),
        ("GET", _P, None),
        ("PATCH", _P, {"approved": "APPROVED"}),
        ("DELETE", _P, None),
        ("GET", f"{_P}/submissions", None),
        ("POST", f"{_P}/submissions", [{"text": "x"}]),
        ("PATCH", f"{_P}/submissions/1", [{"text": "x"}]),
        ("GET", _R, None),
        ("POST", _R, _REVIEW),
        ("PATCH", _R, _REVIEW),
        ("PUT", _R, _REVIEW),
        ("DELETE", _R, None),
        ("GET", f"{_T}/progress", None),
        ("POST", f"{_T}/stages/effect", {"stages": []}),
        ("GET", f"{_T}/files", None),
        ("GET", f"{_T}/files/raw?name=a.txt", None),
        ("GET", f"{_T}/versions", None),
        ("POST", f"{_T}/files/uploads", {"name": "a.txt", "size": 1}),
        ("DELETE", f"{_T}/files?name=a.txt", None),
        ("DELETE", f"{_T}/starter", None),
        ("GET", f"{_T}/inheritance", None),
        ("GET", f"{_T}/teams", None),
        ("POST", "/task-drafts", {"task": 1}),
    ],
)
def test_a_signed_out_caller_is_asked_to_sign_in(
    api_client: TestClient, method: str, path: str, body
):
    """Every action on a problem asks a signed-out caller to sign in first."""
    resp = api_client.request(method, path, json=body)
    assert resp.status_code == 401, resp.text
