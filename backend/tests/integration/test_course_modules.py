"""The module switches a course carries, end to end.

What this file is really pinning is the shape of the promise: the switches come
back with the board (so the sidebar can filter on them without a second call),
an absent key means the module is shown (so today's boards change behaviour not
at all), and only the course's admins can move them.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, create_approved_space, unique_int


class TestCourseModules:
    @pytest.fixture
    def setup_space(self, user_client: UserCreator, api_client: TestClient) -> dict:
        creator = user_client.create_user()
        creator.token = user_client.login(
            api_client, creator.username, creator.password
        )
        resp = create_approved_space(
            api_client,
            json={
                "name": f"Modules Space ({unique_int()})",
                "intro": "intro",
                "description": "description",
                "announcements": [],
                "taskTemplates": [],
            },
            headers={"Authorization": f"Bearer {creator.token}"},
        )
        assert resp.status_code == 201
        return {
            "creator": creator,
            "space_id": resp.json()["data"]["space"]["id"],
            "created": resp.json()["data"]["space"],
        }

    def test_a_new_board_declares_nothing(self, setup_space: dict):
        """`{}` = every module shown, which is what a board did yesterday."""
        assert setup_space["created"]["courseModules"] == {}

    def test_a_declared_switch_comes_back_with_the_board(
        self, setup_space: dict, api_client: TestClient
    ):
        space_id = setup_space["space_id"]
        headers = {"Authorization": f"Bearer {setup_space['creator'].token}"}
        resp = api_client.patch(
            f"/spaces/{space_id}",
            json={"courseModules": {"quiz": False, "team": True}},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["space"]["courseModules"] == {
            "quiz": False,
            "team": True,
        }
        # And on the read path, from a separate request: the sidebar reads the
        # board it already has, it does not ask again.
        fetched = api_client.get(f"/spaces/{space_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["data"]["space"]["courseModules"] == {
            "quiz": False,
            "team": True,
        }

    def test_a_patch_that_says_nothing_about_modules_leaves_them_alone(
        self, setup_space: dict, api_client: TestClient
    ):
        space_id = setup_space["space_id"]
        headers = {"Authorization": f"Bearer {setup_space['creator'].token}"}
        api_client.patch(
            f"/spaces/{space_id}",
            json={"courseModules": {"quiz": False}},
            headers=headers,
        )
        renamed = api_client.patch(
            f"/spaces/{space_id}",
            json={"name": f"Renamed ({unique_int()})"},
            headers=headers,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["data"]["space"]["courseModules"] == {"quiz": False}

    def test_a_module_that_does_not_exist_is_refused(
        self, setup_space: dict, api_client: TestClient
    ):
        """A person is looking at this form, so a typo is answered, not dropped."""
        resp = api_client.patch(
            f"/spaces/{setup_space['space_id']}",
            json={"courseModules": {"quizes": False}},
            headers={"Authorization": f"Bearer {setup_space['creator'].token}"},
        )
        assert resp.status_code == 400, resp.text
        assert "quizes" in resp.text

    def test_a_non_boolean_is_refused(self, setup_space: dict, api_client: TestClient):
        resp = api_client.patch(
            f"/spaces/{setup_space['space_id']}",
            json={"courseModules": {"quiz": "no"}},
            headers={"Authorization": f"Bearer {setup_space['creator'].token}"},
        )
        assert resp.status_code == 400, resp.text
        assert "quiz" in resp.text

    def test_only_the_courses_admins_move_the_switches(
        self, setup_space: dict, api_client: TestClient, user_client: UserCreator
    ):
        student = user_client.create_user()
        student.token = user_client.login(
            api_client, student.username, student.password
        )
        resp = api_client.patch(
            f"/spaces/{setup_space['space_id']}",
            json={"courseModules": {"quiz": False}},
            headers={"Authorization": f"Bearer {student.token}"},
        )
        assert resp.status_code == 403, resp.text
        # And nothing moved.
        fetched = api_client.get(
            f"/spaces/{setup_space['space_id']}",
            headers={"Authorization": f"Bearer {setup_space['creator'].token}"},
        )
        assert fetched.json()["data"]["space"]["courseModules"] == {}
