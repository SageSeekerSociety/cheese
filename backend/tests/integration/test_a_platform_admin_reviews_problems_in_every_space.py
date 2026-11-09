"""A platform admin reviews 题目 waiting in any space, without a seat in it.

The space's own reviewers (whoever teaches it) review from the space; the
platform's admins review from ``/admin/tasks``, which lists what waits in every
space. Anyone else is refused there.
"""

import pytest

from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import create_approved_space

REVIEWER = "task-queue-reviewer"


@pytest.fixture
def people(client, monkeypatch):
    owner = {"Authorization": f"Bearer {seed_user(client, 'task-queue-owner')}"}
    stranger = {"Authorization": f"Bearer {seed_user(client, 'task-queue-stranger')}"}
    admin = {"Authorization": f"Bearer {seed_user(client, REVIEWER)}"}
    monkeypatch.setattr(settings, "platform_admin_handles", [REVIEWER])
    return owner, stranger, admin


def _space(client, owner) -> int:
    made = create_approved_space(
        client,
        json={"name": "一门课", "intro": "每周练习", "avatarId": 1},
        headers=owner,
    )
    assert made.status_code == 201, made.text
    return made.json()["data"]["space"]["id"]


def _publish(client, owner, space_id: int, name: str) -> int:
    made = client.post(
        "/tasks",
        json={
            "name": name,
            "space": space_id,
            "intro": "题",
            "description": '{"type":"doc","content":[]}',
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
        },
        headers=owner,
    )
    assert made.status_code == 200, made.text
    return made.json()["data"]["task"]["id"]


def _queue(client, headers) -> dict[int, dict]:
    listed = client.get("/admin/tasks", headers=headers)
    assert listed.status_code == 200, listed.text
    return {task["id"]: task for task in listed.json()["data"]["tasks"]}


def test_a_platform_admin_approves_a_problem_in_a_space_they_are_not_in(client, people):
    owner, _stranger, admin = people
    space_id = _space(client, owner)
    task_id = _publish(client, owner, space_id, "等平台审的题")

    waiting = _queue(client, admin)
    assert task_id in waiting
    assert waiting[task_id]["space"]["id"] == space_id

    approved = client.post(
        f"/admin/tasks/{task_id}/review", json={"approved": "APPROVED"}, headers=admin
    )
    assert approved.status_code == 200, approved.text
    task = client.get(f"/tasks/{task_id}", headers=owner).json()["data"]["task"]
    assert task["approved"] == "APPROVED"
    assert task_id not in _queue(client, admin)


def test_a_platform_admin_sends_a_problem_back_with_a_reason(client, people):
    owner, _stranger, admin = people
    task_id = _publish(client, owner, _space(client, owner), "要退回的题")

    rejected = client.post(
        f"/admin/tasks/{task_id}/review",
        json={"approved": "DISAPPROVED", "rejectReason": "说明不完整"},
        headers=admin,
    )
    assert rejected.status_code == 200, rejected.text
    task = client.get(f"/tasks/{task_id}", headers=owner).json()["data"]["task"]
    assert (task["approved"], task["rejectReason"]) == ("DISAPPROVED", "说明不完整")


def test_someone_who_is_not_a_platform_admin_neither_sees_nor_reviews_the_queue(
    client, people
):
    owner, stranger, _admin = people
    task_id = _publish(client, owner, _space(client, owner), "别人碰不到的题")

    for who in (owner, stranger):
        assert client.get("/admin/tasks", headers=who).status_code == 403
        refused = client.post(
            f"/admin/tasks/{task_id}/review",
            json={"approved": "APPROVED"},
            headers=who,
        )
        assert refused.status_code == 403, refused.text
    task = client.get(f"/tasks/{task_id}", headers=owner).json()["data"]["task"]
    assert task["approved"] == "NONE"
