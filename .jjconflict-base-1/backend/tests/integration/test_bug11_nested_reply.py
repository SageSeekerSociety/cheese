"""Bug #11 regression: nested reply (reply-to-reply) not in top-level sub list.

NT DiscussionController.getDiscussion returns subDiscussions filtered by
parentId = discussionId, which only yields direct children. A reply whose
parent is another reply (grandchild) does NOT appear in the top-level
subDiscussions list. This is by design: the frontend loads deeper nesting
via GET /discussions/{replyId}/sub-discussions.

This test confirms the Python backend matches this NT behavior.
"""

import time

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import UserCreator, unique_int


class TestBug11NestedReplyByDesign:
    @pytest.fixture
    def setup(self, user_client: UserCreator, api_client: TestClient) -> dict:
        user = user_client.create_user()
        user.token = user_client.login(api_client, user.username, user.password)
        headers = {"Authorization": f"Bearer {user.token}"}

        suffix = unique_int()
        space_resp = api_client.post(
            "/spaces",
            json={
                "name": f"Bug11 Space ({suffix})",
                "intro": "Test",
                "description": "Desc",
                "avatarId": 1,
            },
            headers=headers,
        )
        assert space_resp.status_code == 201
        space_id = space_resp.json()["data"]["space"]["id"]
        category_id = space_resp.json()["data"]["space"]["defaultCategoryId"]

        deadline = int(time.time() * 1000) + 7 * 86400 * 1000
        task_resp = api_client.post(
            "/tasks",
            json={
                "name": f"Bug11 Task ({suffix})",
                "intro": "Test",
                "description": '{"type":"doc","content":[]}',
                "space": space_id,
                "categoryId": category_id,
                "submitterType": "USER",
                "resubmittable": True,
                "editable": True,
                "defaultDeadline": 30,
                "deadline": deadline,
            },
            headers=headers,
        )
        assert task_resp.status_code == 200
        task_id = task_resp.json()["data"]["task"]["id"]

        # Create root discussion
        root_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "TASK",
                "modelId": task_id,
                "content": "Root discussion",
            },
            headers=headers,
        )
        assert root_resp.status_code == 201
        root_id = root_resp.json()["data"]["discussion"]["id"]

        # Create a direct reply (child)
        reply_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "TASK",
                "modelId": task_id,
                "content": "Direct reply",
                "parentId": root_id,
            },
            headers=headers,
        )
        assert reply_resp.status_code == 201
        reply_id = reply_resp.json()["data"]["discussion"]["id"]

        # Create a nested reply (grandchild: parent = reply_id)
        nested_resp = api_client.post(
            "/discussions",
            json={
                "modelType": "TASK",
                "modelId": task_id,
                "content": "Nested reply to reply",
                "parentId": reply_id,
            },
            headers=headers,
        )
        assert nested_resp.status_code == 201
        nested_id = nested_resp.json()["data"]["discussion"]["id"]

        return {
            "headers": headers,
            "root_id": root_id,
            "reply_id": reply_id,
            "nested_id": nested_id,
        }

    def test_get_discussion_only_shows_direct_children(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """GET /discussions/{rootId} subDiscussions only lists direct children.

        The nested reply (grandchild) should NOT appear here.
        """
        resp = api_client.get(
            f"/discussions/{setup['root_id']}",
            headers=setup["headers"],
        )
        assert resp.status_code == 200
        data = resp.json()["data"]
        sub_ids = [d["id"] for d in data["subDiscussions"]["discussions"]]
        # Direct reply IS in subDiscussions
        assert setup["reply_id"] in sub_ids
        # Nested reply is NOT in top-level subDiscussions
        assert setup["nested_id"] not in sub_ids

    def test_nested_reply_visible_via_sub_discussions_endpoint(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """GET /discussions/{replyId}/sub-discussions shows the nested reply."""
        resp = api_client.get(
            f"/discussions/{setup['reply_id']}/sub-discussions",
            headers=setup["headers"],
        )
        assert resp.status_code == 200
        sub_ids = [d["id"] for d in resp.json()["data"]["discussions"]]
        assert setup["nested_id"] in sub_ids

    def test_nested_reply_persists_after_creation(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """The nested reply can be fetched individually."""
        resp = api_client.get(
            f"/discussions/{setup['nested_id']}",
            headers=setup["headers"],
        )
        assert resp.status_code == 200
        d = resp.json()["data"]["discussion"]
        assert d["id"] == setup["nested_id"]
        assert d["parentId"] == setup["reply_id"]
