"""
Integration tests for the User Profile module.
Migrated from cheese-backend/test/user.profile.e2e-spec.ts
"""

from __future__ import annotations

import random
import io

import httpx
import pytest

from tests.integration.conftest import CreatedUser, UserCreator


class TestUserProfileIntegration:
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
        self.profile_prefix = f"P{random.randint(100000, 999999)}"

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def _upload_avatar(self) -> int:
        fake_image = io.BytesIO(b"fake image content for avatar")
        response = self.client.post(
            "/avatars",
            headers=self.headers,
            files={"avatar": ("test.jpg", fake_image, "image/jpeg")},
        )
        assert response.status_code == 201
        return response.json()["data"]["avatarId"]

    def test_update_user_profile(self):
        new_nickname = f"{self.profile_prefix}_updated"
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={
                "nickname": new_nickname,
                "intro": f"{self.profile_prefix} test user updated",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200

    def test_update_user_profile_with_avatar(self):
        avatar_id = self._upload_avatar()
        new_nickname = f"{self.profile_prefix}_avatar"
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={
                "nickname": new_nickname,
                "intro": "test user with avatar",
                "avatarId": avatar_id,
            },
        )
        assert response.status_code == 200

        get_resp = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        user_data = get_resp.json()["data"]["user"]
        assert user_data["avatarId"] == avatar_id

    def test_update_user_profile_invalid_token(self):
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers={"Authorization": "Bearer invalid-token"},
            json={"nickname": "hacked"},
        )
        assert response.status_code == 401

    def test_update_user_profile_no_auth(self):
        response = self.client.put(
            f"/users/{self.user.user_id}",
            json={"nickname": "hacked"},
        )
        assert response.status_code == 401

    def test_update_user_profile_not_owner(self):
        aux_user, aux_headers = self._create_aux_user()
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=aux_headers,
            json={"nickname": "hacked"},
        )
        assert response.status_code == 403

    def test_get_user_profile(self):
        new_nickname = f"{self.profile_prefix}_get"
        new_intro = f"{self.profile_prefix} intro text"
        self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={"nickname": new_nickname, "intro": new_intro},
        )

        response = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        user_data = response.json()["data"]["user"]
        assert user_data["username"] == self.user.username
        assert new_nickname in user_data["nickname"]
        assert new_intro in user_data["intro"]
        assert "follow_count" in user_data
        assert "fans_count" in user_data
        assert "question_count" in user_data
        assert "answer_count" in user_data
        assert "is_follow" in user_data

    def test_get_user_profile_not_found(self):
        response = self.client.get(
            "/users/999999",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_user_profile_no_auth(self):
        response = self.client.get(f"/users/{self.user.user_id}")
        assert response.status_code == 401

    def test_update_nickname_only(self):
        new_nickname = f"{self.profile_prefix}_nickname"
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={"nickname": new_nickname},
        )
        assert response.status_code == 200

        get_resp = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        assert new_nickname in get_resp.json()["data"]["user"]["nickname"]

    def test_update_intro_only(self):
        new_intro = f"{self.profile_prefix} new intro"
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={"intro": new_intro},
        )
        assert response.status_code == 200

        get_resp = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        assert new_intro in get_resp.json()["data"]["user"]["intro"]

    def test_update_long_intro(self):
        long_intro = "这是一个很长的介绍" * 100
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={"intro": long_intro},
        )
        assert response.status_code == 200

    def test_update_intro_with_emoji(self):
        emoji_intro = f"{self.profile_prefix} 我喜欢编程 😂🎉💻"
        response = self.client.put(
            f"/users/{self.user.user_id}",
            headers=self.headers,
            json={"intro": emoji_intro},
        )
        assert response.status_code == 200

        get_resp = self.client.get(
            f"/users/{self.user.user_id}",
            headers=self.headers,
        )
        assert "😂" in get_resp.json()["data"]["user"]["intro"]
