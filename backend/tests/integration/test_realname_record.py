"""What a real-name record must hold, and what filling it in is for.

A real name and a student ID are what a course needs to know who took part;
grade, major and class help its admins sort people, and nobody is required to
give them.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator
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
