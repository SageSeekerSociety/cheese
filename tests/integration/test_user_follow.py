"""
Integration tests for the User Follow module.
Migrated from cheese-backend/test/user.follow.e2e-spec.ts (436 lines, 20 tests)
Complete equivalence migration.
"""

import httpx
import pytest

from tests.integration.conftest import CreatedUser, UserCreator


class TestUserFollowLogicIntegration:
    """Tests for user follow logic."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.aux_user_ids: list[int] = []
        self.aux_user_tokens: list[str] = []
        for _ in range(10):
            user, headers = self._create_aux_user()
            self.aux_user_ids.append(user.user_id)
            self.aux_user_tokens.append(user.token)

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_follow_no_auth(self):
        response = self.client.post(f"/users/{self.aux_user_ids[0]}/followers")
        assert response.status_code == 401

    def test_follow_user_not_found(self):
        response = self.client.post(
            f"/users/{self.aux_user_ids[0] + 1000000000}/followers",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_follow_yourself(self):
        response = self.client.post(
            f"/users/{self.user.user_id}/followers",
            headers=self.headers,
        )
        assert response.status_code == 422

    def test_follow_users_successfully(self):
        for user_id in self.aux_user_ids:
            response = self.client.post(
                f"/users/{user_id}/followers",
                headers=self.headers,
            )
            assert response.status_code == 201
            assert response.json()["code"] == 201

    def test_reverse_follow_main_user(self):
        for token in self.aux_user_tokens:
            response = self.client.post(
                f"/users/{self.user.user_id}/followers",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert response.status_code == 201
            assert response.json()["code"] == 201

    def test_get_user_statistics_from_self(self):
        for user_id in self.aux_user_ids:
            self.client.post(f"/users/{user_id}/followers", headers=self.headers)
        for token in self.aux_user_tokens:
            self.client.post(
                f"/users/{self.user.user_id}/followers",
                headers={"Authorization": f"Bearer {token}"},
            )

        response = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["follow_count"] == len(self.aux_user_ids)
        assert user_data["fans_count"] == len(self.aux_user_ids)
        assert user_data["is_follow"] is False

    def test_get_user_statistics_from_follower(self):
        for user_id in self.aux_user_ids:
            self.client.post(f"/users/{user_id}/followers", headers=self.headers)
        for token in self.aux_user_tokens:
            self.client.post(
                f"/users/{self.user.user_id}/followers",
                headers={"Authorization": f"Bearer {token}"},
            )

        response = self.client.get(
            f"/users/{self.user.user_id}",
            headers={"Authorization": f"Bearer {self.aux_user_tokens[0]}"},
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["follow_count"] == len(self.aux_user_ids)
        assert user_data["fans_count"] == len(self.aux_user_ids)
        assert user_data["is_follow"] is True

    def test_get_aux_user_statistics(self):
        for user_id in self.aux_user_ids:
            self.client.post(f"/users/{user_id}/followers", headers=self.headers)
        for token in self.aux_user_tokens:
            self.client.post(
                f"/users/{self.user.user_id}/followers",
                headers={"Authorization": f"Bearer {token}"},
            )

        response = self.client.get(
            f"/users/{self.aux_user_ids[0]}",
            headers=self.headers,
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["follow_count"] == 1
        assert user_data["fans_count"] == 1
        assert user_data["is_follow"] is True

    def test_follow_already_followed(self):
        self.client.post(
            f"/users/{self.aux_user_ids[0]}/followers",
            headers=self.headers,
        )
        response = self.client.post(
            f"/users/{self.aux_user_ids[0]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 422

    def test_unfollow_user_successfully(self):
        self.client.post(
            f"/users/{self.aux_user_ids[-1]}/followers",
            headers=self.headers,
        )
        response = self.client.delete(
            f"/users/{self.aux_user_ids[-1]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert response.json()["code"] == 200

    def test_unfollow_not_followed(self):
        response = self.client.delete(
            f"/users/{self.aux_user_ids[-1]}/followers",
            headers=self.headers,
        )
        assert response.status_code == 422


class TestUserFollowersListIntegration:
    """Tests for getting followers list."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.aux_user_ids: list[int] = []
        self.aux_user_tokens: list[str] = []
        for _ in range(10):
            user, headers = self._create_aux_user()
            self.aux_user_ids.append(user.user_id)
            self.aux_user_tokens.append(user.token)
        for user_id in self.aux_user_ids:
            self.client.post(f"/users/{user_id}/followers", headers=self.headers)
        for token in self.aux_user_tokens:
            self.client.post(
                f"/users/{self.user.user_id}/followers",
                headers={"Authorization": f"Bearer {token}"},
            )

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_followers_of_aux_user(self):
        for i, user_id in enumerate(self.aux_user_ids[:-1]):
            response = self.client.get(
                f"/users/{user_id}/followers",
                headers=self.headers,
            )
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert len(data["data"]["users"]) == 1
            assert data["data"]["users"][0]["id"] == self.user.user_id
            assert data["data"]["page"]["page_start"] == self.user.user_id
            assert data["data"]["page"]["page_size"] == 1
            assert data["data"]["page"]["has_prev"] is False
            assert data["data"]["page"]["prev_start"] == 0
            assert data["data"]["page"]["has_more"] is False
            assert data["data"]["page"]["next_start"] == 0

    def test_get_followers_of_aux_user_with_pagination(self):
        for user_id in self.aux_user_ids[:-1]:
            response = self.client.get(
                f"/users/{user_id}/followers",
                headers=self.headers,
                params={"page_start": self.user.user_id, "page_size": 1},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["code"] == 200
            assert len(data["data"]["users"]) == 1
            assert data["data"]["users"][0]["id"] == self.user.user_id
            assert data["data"]["page"]["page_start"] == self.user.user_id
            assert data["data"]["page"]["page_size"] == 1
            assert data["data"]["page"]["has_prev"] is False
            assert data["data"]["page"]["prev_start"] == 0
            assert data["data"]["page"]["has_more"] is False
            assert data["data"]["page"]["next_start"] == 0

    def test_get_followers_of_main_user(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/followers",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["users"]) == len(self.aux_user_tokens)
        assert data["data"]["users"][0]["id"] == self.aux_user_ids[0]
        assert data["data"]["users"][0]["avatarId"] is not None
        assert data["data"]["page"]["page_start"] == self.aux_user_ids[0]
        assert data["data"]["page"]["page_size"] == len(self.aux_user_tokens)
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is False
        assert data["data"]["page"]["next_start"] == 0

    def test_get_followers_with_pagination_middle(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/followers",
            headers=self.headers,
            params={"page_start": self.aux_user_ids[3], "page_size": 3},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["users"]) == 3
        assert data["data"]["users"][0]["id"] == self.aux_user_ids[3]
        assert data["data"]["users"][1]["id"] == self.aux_user_ids[4]
        assert data["data"]["users"][2]["id"] == self.aux_user_ids[5]
        assert data["data"]["page"]["page_start"] == self.aux_user_ids[3]
        assert data["data"]["page"]["page_size"] == 3
        assert data["data"]["page"]["has_prev"] is True
        assert data["data"]["page"]["prev_start"] == self.aux_user_ids[0]
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.aux_user_ids[6]


class TestUserFollowingListIntegration:
    """Tests for getting following list."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.aux_user_ids: list[int] = []
        self.aux_user_tokens: list[str] = []
        for _ in range(9):
            user, headers = self._create_aux_user()
            self.aux_user_ids.append(user.user_id)
            self.aux_user_tokens.append(user.token)
        for user_id in self.aux_user_ids:
            self.client.post(f"/users/{user_id}/followers", headers=self.headers)

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_following_list(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/follow/users",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["users"]) == 9
        assert data["data"]["users"][0]["id"] == self.aux_user_ids[0]
        assert data["data"]["page"]["page_start"] == self.aux_user_ids[0]
        assert data["data"]["page"]["page_size"] == 9
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is False
        assert data["data"]["page"]["next_start"] == 0

    def test_get_following_list_pagination_first_page(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/follow/users",
            headers=self.headers,
            params={"page_start": self.aux_user_ids[0], "page_size": 1},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["users"]) == 1
        assert data["data"]["users"][0]["id"] == self.aux_user_ids[0]
        assert data["data"]["page"]["page_start"] == self.aux_user_ids[0]
        assert data["data"]["page"]["page_size"] == 1
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.aux_user_ids[1]

    def test_get_following_list_pagination_middle(self):
        response = self.client.get(
            f"/users/{self.user.user_id}/follow/users",
            headers=self.headers,
            params={"page_start": self.aux_user_ids[2], "page_size": 2},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["users"]) == 2
        assert data["data"]["users"][0]["id"] == self.aux_user_ids[2]
        assert data["data"]["page"]["page_start"] == self.aux_user_ids[2]
        assert data["data"]["page"]["page_size"] == 2
        assert data["data"]["page"]["has_prev"] is True
        assert data["data"]["page"]["prev_start"] == self.aux_user_ids[0]
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.aux_user_ids[4]


class TestUserFollowStatisticsIntegration:
    """Tests for user follow statistics."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: httpx.Client,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.aux_user_ids: list[int] = []
        self.aux_user_tokens: list[str] = []
        for _ in range(9):
            user, headers = self._create_aux_user()
            self.aux_user_ids.append(user.user_id)
            self.aux_user_tokens.append(user.token)
        for user_id in self.aux_user_ids:
            self.client.post(f"/users/{user_id}/followers", headers=self.headers)
        for token in self.aux_user_tokens:
            self.client.post(
                f"/users/{self.user.user_id}/followers",
                headers={"Authorization": f"Bearer {token}"},
            )
        self.client.delete(
            f"/users/{self.aux_user_ids[-1]}/followers",
            headers=self.headers,
        )

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_main_user_statistics_from_self(self):
        response = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["follow_count"] == len(self.aux_user_ids) - 1
        assert user_data["fans_count"] == len(self.aux_user_ids)
        assert user_data["is_follow"] is False

    def test_aux_user_statistics_from_main(self):
        response = self.client.get(
            f"/users/{self.aux_user_ids[0]}",
            headers=self.headers,
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["follow_count"] == 1
        assert user_data["fans_count"] == 1
        assert user_data["is_follow"] is True

    def test_aux_user_statistics_from_main_again(self):
        response = self.client.get(
            f"/users/{self.aux_user_ids[0]}",
            headers=self.headers,
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["follow_count"] == 1
        assert user_data["fans_count"] == 1
        assert user_data["is_follow"] is True

    def test_get_followers_no_auth(self):
        response = self.client.get(f"/users/{self.aux_user_ids[0]}/followers")
        assert response.status_code == 401
