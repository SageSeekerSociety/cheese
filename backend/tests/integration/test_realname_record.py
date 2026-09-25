"""What a real-name record must hold, and what filling it in is for.

A real name and a student ID are what a course needs to know who took part;
grade, major and class help its admins sort people, and nobody is required to
give them.
"""

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.domain.user.models import UserRealNameIdentity
from app.domain.user.realname_services import realname_dict
from tests.integration.conftest import UserCreator, create_approved_space, unique_int
from tests.integration.test_realname_sudo import IDENTITY, _Owner


@pytest.fixture
def owner(user_client: UserCreator, api_client: TestClient) -> _Owner:
    return _Owner(user_client)


def _save(owner: _Owner, client: TestClient, method: str, body: dict):
    return getattr(owner, method)(
        client, body, owner.sudo_ticket(client, "realname:update")
    )


def _stored(owner: _Owner, client: TestClient) -> dict | None:
    ticket = owner.sudo_ticket(client, "realname:view")
    resp = owner.read(client, precise=True, ticket=ticket)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["identity"]


class TestWhatARecordHolds:
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_grade_major_and_class_may_be_left_empty(
        self, owner: _Owner, api_client: TestClient, method: str
    ):
        body = {**IDENTITY, "grade": "", "major": "", "className": ""}

        resp = _save(owner, api_client, method, body)

        assert resp.status_code == 200, resp.text
        assert _stored(owner, api_client) == body

    @pytest.mark.parametrize("missing", ["realName", "studentId"])
    @pytest.mark.parametrize("method", ["put", "patch"])
    def test_a_name_and_a_student_id_are_required(
        self, owner: _Owner, api_client: TestClient, method: str, missing: str
    ):
        resp = _save(owner, api_client, method, {**IDENTITY, missing: "  "})

        assert resp.status_code == 400, resp.text
        assert _stored(owner, api_client) is None

    def test_an_optional_field_can_be_cleared(
        self, owner: _Owner, api_client: TestClient
    ):
        assert _save(owner, api_client, "put", IDENTITY).status_code == 200

        resp = _save(owner, api_client, "patch", {"className": ""})

        assert resp.status_code == 200, resp.text
        assert _stored(owner, api_client) == {**IDENTITY, "className": ""}


def _board(admin: _Owner, client: TestClient, *, name: str) -> dict:
    resp = create_approved_space(
        client,
        json={"name": name, "intro": "i", "description": "d", "avatarId": 1},
        headers=admin.headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["data"]["space"]


def _task_requiring_real_name(admin: _Owner, client: TestClient, space: dict) -> int:
    resp = client.post(
        "/tasks",
        json={
            "name": f"Real-name task ({unique_int()})",
            "intro": "i",
            "description": '{"type":"doc","content":[]}',
            "space": space["id"],
            "categoryId": space["defaultCategoryId"],
            "submitterType": "USER",
            "resubmittable": True,
            "editable": True,
            "defaultDeadline": 30,
            "deadline": int(time.time() * 1000) + 7 * 86400 * 1000,
            "requireRealName": True,
        },
        headers=admin.headers,
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["task"]["id"]
    approved = client.patch(
        f"/tasks/{task_id}", json={"approved": "APPROVED"}, headers=admin.headers
    )
    assert approved.status_code == 200, approved.text
    return task_id


def _missing_real_name(person: _Owner, client: TestClient, task_id: int) -> bool:
    resp = client.get(
        f"/tasks/{task_id}",
        params={"queryJoinability": "true"},
        headers=person.headers,
    )
    assert resp.status_code == 200, resp.text
    user = resp.json()["data"]["task"]["participationEligibility"]["user"]
    return "MISSING_REAL_NAME" in [reason["code"] for reason in user["reasons"]]


def _join(admin: _Owner, client: TestClient, space: dict, person: _Owner) -> None:
    resp = client.post(
        f"/spaces/{space['id']}/members",
        json={"userId": person.id},
        headers=admin.headers,
    )
    assert resp.status_code == 201, resp.text


class TestDeletingARecord:
    def test_a_task_requiring_real_name_refuses_the_person_afterwards(
        self, owner: _Owner, user_client: UserCreator, api_client: TestClient
    ):
        admin = _Owner(user_client)
        space = _board(admin, api_client, name=f"Course ({unique_int()})")
        _join(admin, api_client, space, owner)
        task_id = _task_requiring_real_name(admin, api_client, space)
        assert _save(owner, api_client, "put", IDENTITY).status_code == 200
        assert not _missing_real_name(owner, api_client, task_id)

        ticket = owner.sudo_ticket(api_client, "realname:delete")
        assert owner.delete(api_client, ticket).status_code == 200

        assert _missing_real_name(owner, api_client, task_id)

    def test_it_can_be_filled_in_again(self, owner: _Owner, api_client: TestClient):
        assert _save(owner, api_client, "put", IDENTITY).status_code == 200
        ticket = owner.sudo_ticket(api_client, "realname:delete")
        assert owner.delete(api_client, ticket).status_code == 200

        again = {**IDENTITY, "realName": "李四"}
        assert _save(owner, api_client, "put", again).status_code == 200

        assert _stored(owner, api_client) == again

    def test_what_it_held_is_erased(
        self, owner: _Owner, api_client: TestClient, db_session, _portal
    ):
        assert _save(owner, api_client, "put", IDENTITY).status_code == 200
        ticket = owner.sudo_ticket(api_client, "realname:delete")
        assert owner.delete(api_client, ticket).status_code == 200

        async def rows():
            result = await db_session.execute(
                select(UserRealNameIdentity).where(
                    UserRealNameIdentity.user_id == owner.id
                )
            )
            return [realname_dict(row) for row in result.scalars()]

        held = _portal.call(rows)
        assert held
        assert all(not any(fields.values()) for fields in held)
