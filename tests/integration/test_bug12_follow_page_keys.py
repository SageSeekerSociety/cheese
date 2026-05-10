"""Bug #12 regression: followers/following page keys must be camelCase.

The frontend Page type expects { pageStart, pageSize, hasMore, nextStart }.
The backend was returning snake_case keys (page_start, page_size, etc.),
causing the frontend paging utility to never see hasMore/nextStart and
rendering blank lists.
"""

import pytest
from fastapi.testclient import TestClient

from tests.integration.conftest import CreatedUser, UserCreator


class TestBug12FollowPageKeyCasing:
    @pytest.fixture
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ) -> dict:
        # User A follows User B
        user_b = user_client.create_user()
        user_b.token = user_client.login(api_client, user_b.username, user_b.password)

        api_client.post(
            f"/users/{user_b.user_id}/followers",
            headers=auth_headers,
        )

        return {
            "user_a": authenticated_user,
            "user_b": user_b,
            "headers_a": auth_headers,
            "headers_b": {"Authorization": f"Bearer {user_b.token}"},
        }

    def test_followers_page_uses_camel_case(self, api_client: TestClient, setup: dict) -> None:
        """GET /users/{id}/followers page object must use camelCase keys."""
        resp = api_client.get(
            f"/users/{setup['user_b'].user_id}/followers",
            headers=setup["headers_a"],
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        # Must have camelCase keys
        assert "pageStart" in page
        assert "pageSize" in page
        assert "hasMore" in page
        assert "nextStart" in page
        # Must NOT have snake_case keys
        assert "page_start" not in page
        assert "page_size" not in page
        assert "has_more" not in page
        assert "next_start" not in page

    def test_following_page_uses_camel_case(self, api_client: TestClient, setup: dict) -> None:
        """GET /users/{id}/follow/users page object must use camelCase keys."""
        resp = api_client.get(
            f"/users/{setup['user_a'].user_id}/follow/users",
            headers=setup["headers_a"],
        )
        assert resp.status_code == 200
        page = resp.json()["data"]["page"]
        assert "pageStart" in page
        assert "pageSize" in page
        assert "hasMore" in page
        assert "nextStart" in page
        assert "page_start" not in page
        assert "page_size" not in page
        assert "has_more" not in page
        assert "next_start" not in page

    def test_followers_query_accepts_camel_case_params(
        self, api_client: TestClient, setup: dict
    ) -> None:
        """Frontend sends pageStart/pageSize as query params."""
        resp = api_client.get(
            f"/users/{setup['user_b'].user_id}/followers",
            params={"pageStart": setup["user_a"].user_id, "pageSize": 10},
            headers=setup["headers_a"],
        )
        assert resp.status_code == 200
        assert len(resp.json()["data"]["users"]) >= 1
