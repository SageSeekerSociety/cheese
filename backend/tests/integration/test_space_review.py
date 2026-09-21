"""Space applications must be approved before they can reach other users."""

import pytest

from app.core.config import settings
from tests.conftest import seed_user
from tests.integration.conftest import session_auth_headers


@pytest.fixture
def actors(client, monkeypatch):
    owner = {"Authorization": f"Bearer {seed_user(client, 'course-owner')}"}
    stranger = {"Authorization": f"Bearer {seed_user(client, 'course-stranger')}"}
    monkeypatch.setattr(settings, "platform_admin_handles", ["course-reviewer"])
    return owner, stranger, session_auth_headers("course-reviewer")


def test_review_controls_visibility_and_task_creation(client, actors):
    owner, stranger, admin = actors
    response = client.post(
        "/spaces",
        json={"name": "Programming course", "intro": "Weekly exercises"},
        headers=owner,
    )
    assert response.status_code == 201, response.text
    space = response.json()["data"]["space"]
    sid = space["id"]
    assert space["reviewStatus"] == "PENDING"
    assert client.get("/spaces", headers=stranger).json()["data"]["spaces"] == []
    for path in (
        f"/spaces/{sid}",
        f"/spaces/{sid}/categories",
        f"/spaces/{sid}/analytics/overview",
    ):
        assert client.get(path, headers=stranger).status_code == 404
    assert (
        client.get("/space-applications", headers=stranger).json()["data"]["items"]
        == []
    )
    assert (
        client.get("/space-applications", headers=owner).json()["data"]["items"][0][
            "id"
        ]
        == sid
    )
    task = {
        "name": "Exercise",
        "space": sid,
        "intro": "Intro",
        "description": "Details",
        "submitterType": "USER",
        "resubmittable": True,
        "editable": True,
    }
    assert client.post("/tasks", json=task, headers=owner).status_code == 400
    assert client.get("/admin/spaces", headers=owner).status_code == 403
    assert (
        client.post(
            f"/admin/spaces/{sid}/review", json={"approved": True}, headers=owner
        ).status_code
        == 403
    )
    assert (
        client.get("/admin/spaces", headers=admin).json()["data"]["items"][0]["id"]
        == sid
    )
    assert (
        client.post(
            f"/admin/spaces/{sid}/review", json={"approved": True}, headers=admin
        ).status_code
        == 200
    )
    assert (
        client.get("/spaces", headers=stranger).json()["data"]["spaces"][0]["id"] == sid
    )
    assert client.get(f"/spaces/{sid}", headers=stranger).status_code == 200
    assert client.post("/tasks", json=task, headers=owner).status_code == 200
    assert (
        client.post(
            f"/admin/spaces/{sid}/review",
            json={"approved": False, "reason": "No"},
            headers=admin,
        ).status_code
        == 409
    )


def test_rejection_requires_reason_and_only_owner_can_resubmit(client, actors):
    owner, stranger, admin = actors
    sid = client.post("/spaces", json={"name": "Course"}, headers=owner).json()["data"][
        "space"
    ]["id"]
    endpoint = f"/admin/spaces/{sid}/review"
    assert (
        client.post(
            endpoint, json={"approved": False, "reason": " "}, headers=admin
        ).status_code
        == 400
    )
    assert (
        client.post(
            endpoint,
            json={"approved": False, "reason": "Explain the course"},
            headers=admin,
        ).status_code
        == 200
    )
    item = client.get("/space-applications", headers=owner).json()["data"]["items"][0]
    assert item["reviewReason"] == "Explain the course"
    assert item["reviewedBy"] == "course-reviewer"
    endpoint = f"/space-applications/{sid}/resubmit"
    body = {"name": "Programming course", "intro": "Weekly practice"}
    assert client.post(endpoint, json=body, headers=stranger).status_code == 404
    response = client.post(endpoint, json=body, headers=owner)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["application"]["reviewStatus"] == "PENDING"
    assert client.post(endpoint, json=body, headers=owner).status_code == 409
    assert (
        client.patch(
            f"/spaces/{sid}", json={"name": "Changed during review"}, headers=owner
        ).status_code
        == 404
    )
