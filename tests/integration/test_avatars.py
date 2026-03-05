"""
Integration tests for the Avatars module.
Migrated from cheese-backend/test/avatars.e2e-spec.ts (187 lines, 10 tests)
Complete equivalence migration.
"""

import io
import random

import httpx
import psycopg2
import pytest
from datetime import datetime, timezone

from app.core.config import settings
from tests.integration.conftest import CreatedUser, UserCreator


def _get_psycopg2_dsn() -> str:
    db_url = settings.database_url
    if db_url.startswith("postgresql+psycopg2://"):
        return db_url.replace("postgresql+psycopg2://", "postgresql://", 1)
    elif db_url.startswith("postgresql+asyncpg://"):
        return db_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    return db_url


def create_predefined_avatars(count: int = 3) -> list[int]:
    now = datetime.now(timezone.utc)
    dsn = _get_psycopg2_dsn()
    conn = psycopg2.connect(dsn)
    avatar_ids = []
    try:
        with conn.cursor() as cur:
            for i in range(count):
                cur.execute(
                    """
                    INSERT INTO avatar (url, name, created_at, avatar_type, usage_count)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        f"/predefined/avatar_{random.randint(1000, 9999)}.jpg",
                        f"predefined_avatar_{i}",
                        now,
                        "predefined",
                        0,
                    ),
                )
                avatar_ids.append(cur.fetchone()[0])
        conn.commit()
        return avatar_ids
    finally:
        conn.close()


class TestAvatarsUploadIntegration:
    """Tests for uploading avatars."""

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
        self.avatar_id: int | None = None

    def test_upload_avatar(self):
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100 + b"fake image content")
        response = self.client.post(
            "/avatars",
            headers=self.headers,
            files={"avatar": ("test.jpg", fake_image, "image/jpeg")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "avatarId" in data["data"]
        self.avatar_id = data["data"]["avatarId"]

    def test_upload_large_avatar(self):
        large_content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50000
        fake_image = io.BytesIO(large_content)
        response = self.client.post(
            "/avatars",
            headers=self.headers,
            files={"avatar": ("large-image.jpg", fake_image, "image/jpeg")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "avatarId" in data["data"]

    def test_upload_avatar_no_auth(self):
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"fake image content")
        response = self.client.post(
            "/avatars",
            files={"avatar": ("test.jpg", fake_image, "image/jpeg")},
        )
        assert response.status_code == 401


class TestAvatarsGetIntegration:
    """Tests for getting avatars."""

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
        fake_image = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100 + b"fake image content")
        upload_resp = self.client.post(
            "/avatars",
            headers=self.headers,
            files={"avatar": ("test.jpg", fake_image, "image/jpeg")},
        )
        self.avatar_id = upload_resp.json()["data"]["avatarId"]

    def test_get_uploaded_avatar(self):
        response = self.client.get(
            f"/avatars/{self.avatar_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert "max-age" in response.headers.get("cache-control", "")
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("image/")
        assert "content-disposition" in response.headers
        assert "inline" in response.headers.get("content-disposition", "")
        assert "content-length" in response.headers
        assert "etag" in response.headers
        assert "last-modified" in response.headers

    def test_get_avatar_not_found(self):
        response = self.client.get(
            "/avatars/1000000",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_avatar_without_auth(self):
        response = self.client.get(f"/avatars/{self.avatar_id}")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert "max-age" in response.headers.get("cache-control", "")
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("image/")
        assert "content-disposition" in response.headers
        assert "inline" in response.headers.get("content-disposition", "")
        assert "content-length" in response.headers
        assert "etag" in response.headers
        assert "last-modified" in response.headers


class TestDefaultAvatarIntegration:
    """Tests for getting default avatar."""

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

    def test_get_default_avatar(self):
        response = self.client.get("/avatars/default")
        assert response.status_code == 200
        assert "cache-control" in response.headers
        assert "max-age" in response.headers.get("cache-control", "")
        content_type = response.headers.get("content-type", "")
        assert content_type.startswith("image/")
        assert "content-disposition" in response.headers
        assert "inline" in response.headers.get("content-disposition", "")
        assert "content-length" in response.headers
        assert "etag" in response.headers
        assert "last-modified" in response.headers


class TestPredefinedAvatarsIntegration:
    """Tests for getting predefined avatar IDs."""

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
        self.predefined_ids = create_predefined_avatars(3)

    def test_get_predefined_avatar_ids(self):
        response = self.client.get(
            "/avatars/",
            headers=self.headers,
            params={"type": "PREDEFINED"},
        )
        assert response.status_code == 200
        data = response.json()
        assert "avatarIds" in data["data"]
        assert len(data["data"]["avatarIds"]) >= 3

    def test_get_avatars_invalid_type(self):
        response = self.client.get(
            "/avatars/",
            headers=self.headers,
            params={"type": "yuiiiiiii"},
        )
        assert response.status_code == 400

    def test_get_predefined_avatars_no_auth(self):
        response = self.client.get(
            "/avatars/",
            params={"type": "PREDEFINED"},
        )
        assert response.status_code == 401
