"""
Integration tests for the Avatars module.
Migrated from cheese-backend/test/avatars.e2e-spec.ts (187 lines, 10 tests)
Complete equivalence migration.
"""

import io
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from anyio.from_thread import BlockingPortal
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.avatars.models import Avatar
from tests.integration.conftest import CreatedUser, UserCreator, unique_int

# A JPEG that is a JPEG all the way down to its magic bytes (SOI + JFIF APP0 +
# EOI), so the server has to read the file to know what it is serving. The PNG
# below likewise carries a real PNG signature.
REAL_JPEG_BYTES = (
    b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xd9"
)
REAL_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def isolate_avatar_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Point avatar storage at this test's own directory.

    Any test whose premise is "this avatar has no file" MUST do this first. The
    real ``AVATAR_STORAGE_DIR`` is one directory shared by every xdist worker and
    never rolled back, while each worker gets its own database — so an id that is
    unused here can very well have a file written by a worker running something
    else. Concretely: the contract suite's DB is not seeded, so its avatar
    sequence starts at 1 and its upload writes ``avatars/1``, which is exactly the
    id the integration suite's seeded *default* avatar occupies. That collision
    turned this file red once already.
    """
    storage = tmp_path / "avatars"
    monkeypatch.setattr("app.api.routes.avatars.AVATAR_STORAGE_DIR", str(storage))
    return storage


def create_avatar_row(
    db_session: AsyncSession,
    portal: BlockingPortal,
) -> int:
    """Insert an avatar row. Writes no file — that is the caller's business."""
    avatar = Avatar(
        url=f"/predefined/avatar_{unique_int(1000, 9999)}.png",
        name="avatar_without_file",
        created_at=datetime.now(UTC),
        avatar_type="predefined",
        usage_count=0,
    )

    async def _do() -> int:
        db_session.add(avatar)
        await db_session.flush()
        return avatar.id

    return portal.call(_do)


def create_predefined_avatars(
    db_session: AsyncSession, portal: BlockingPortal, count: int = 3
) -> list[int]:
    now = datetime.now(UTC)
    avatars = [
        Avatar(
            url=f"/predefined/avatar_{unique_int(1000, 9999)}.jpg",
            name=f"predefined_avatar_{i}",
            created_at=now,
            avatar_type="predefined",
            usage_count=0,
        )
        for i in range(count)
    ]

    async def _do() -> list[int]:
        for av in avatars:
            db_session.add(av)
        await db_session.flush()
        return [av.id for av in avatars]

    return portal.call(_do)


class TestAvatarsUploadIntegration:
    """Tests for uploading avatars."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.avatar_id: int | None = None

    def test_upload_avatar(self):
        fake_image = io.BytesIO(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100 + b"fake image content"
        )
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
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        fake_image = io.BytesIO(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 100 + b"fake image content"
        )
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

    def test_if_none_match_answers_304(self):
        """带着 ETag 回来问的客户端，拿到 304 而不是再收一遍字节。

        `ETag` 发出去很久了，而**没有任何地方读回它** —— 于是刷新、缓存被驱逐、
        换一台设备共享缓存这些真正会走「再验证一次」的路径，每次都被重新灌一遍
        整张图。这一条钉的就是那个读回：同一个 ETag 再来一次必须是 304、没有正
        文、且仍然带缓存头（否则中间缓存会把 304 当成不可缓存）。

        **反面也要立住**：换一个 ETag 必须回 200，而墓碑式的「只要带 If-None-Match
        就 304」会让所有人永远看不到新换的头像。
        """
        first = self.client.get(f"/avatars/{self.avatar_id}", headers=self.headers)
        assert first.status_code == 200
        etag = first.headers["etag"]

        again = self.client.get(
            f"/avatars/{self.avatar_id}",
            headers={**self.headers, "If-None-Match": etag},
        )
        assert again.status_code == 304
        assert again.content == b""
        assert again.headers["etag"] == etag
        assert "max-age=31536000" in again.headers["cache-control"]

        # 通配与弱标签都认（浏览器与代理实际会发的两种写法）。
        for form in ("*", f"W/{etag}", f'"stale-thing", {etag}'):
            assert (
                self.client.get(
                    f"/avatars/{self.avatar_id}",
                    headers={**self.headers, "If-None-Match": form},
                ).status_code
                == 304
            ), form

        # 对不上的那一份：照常给正文。
        stale = self.client.get(
            f"/avatars/{self.avatar_id}",
            headers={**self.headers, "If-None-Match": '"not-this-one"'},
        )
        assert stale.status_code == 200
        assert stale.content == first.content

    def _upload(self, filename: str, content: bytes, content_type: str) -> int:
        response = self.client.post(
            "/avatars",
            headers=self.headers,
            files={"avatar": (filename, io.BytesIO(content), content_type)},
        )
        assert response.status_code == 201
        return response.json()["data"]["avatarId"]

    def test_get_avatar_with_missing_file_is_404_and_not_cached(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """A row in the DB with no file on disk must not be served as an image.

        It used to answer 200 with a PNG signature followed by 100 zero bytes —
        no IHDR, so it could never decode — and cached that for a year.
        """
        isolate_avatar_storage(monkeypatch, tmp_path)
        avatar_id = create_avatar_row(self.db, self.portal)

        response = self.client.get(f"/avatars/{avatar_id}", headers=self.headers)

        assert response.status_code == 404
        assert "max-age=31536000" not in response.headers.get("cache-control", "")

    def test_get_uploaded_jpeg_reports_jpeg_and_stays_cacheable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Content type comes from the bytes; the long cache stays on real files."""
        isolate_avatar_storage(monkeypatch, tmp_path)
        avatar_id = self._upload(
            "photo.bin", REAL_JPEG_BYTES, "application/octet-stream"
        )

        response = self.client.get(f"/avatars/{avatar_id}", headers=self.headers)

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("image/jpeg")
        assert "max-age=31536000" in response.headers.get("cache-control", "")

    def test_get_uploaded_png_reports_png(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        isolate_avatar_storage(monkeypatch, tmp_path)
        avatar_id = self._upload(
            "photo.bin", REAL_PNG_BYTES, "application/octet-stream"
        )

        response = self.client.get(f"/avatars/{avatar_id}", headers=self.headers)

        assert response.status_code == 200
        assert response.headers.get("content-type", "").startswith("image/png")

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
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal

    def _default_avatar_id(self) -> int:
        response = self.client.get("/avatars/default/id", headers=self.headers)
        assert response.status_code == 200
        return response.json()["data"]["avatarId"]

    def test_get_default_avatar_without_file_is_404_and_not_cached(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """The seeded default avatar is a DB row with no image file behind it.

        This test used to assert 200 + a long ``max-age`` here, which meant it
        was pinning the bug: the seed migration inserts avatar rows but never
        writes any file, so this endpoint was always taking the fabricated
        108-byte "PNG" branch — content that can never decode, handed out with a
        one-year cache. That is issue #417, so the expectation is now 404 with no
        year-long cache.
        """
        isolate_avatar_storage(monkeypatch, tmp_path)

        response = self.client.get("/avatars/default")

        assert response.status_code == 404
        assert "max-age=31536000" not in response.headers.get("cache-control", "")

    def test_get_default_avatar_with_file_is_served_and_cacheable(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """The other half: when the file is really there, nothing changed."""
        storage = isolate_avatar_storage(monkeypatch, tmp_path)
        os.makedirs(storage, exist_ok=True)
        (storage / str(self._default_avatar_id())).write_bytes(REAL_PNG_BYTES)

        response = self.client.get("/avatars/default")

        assert response.status_code == 200
        assert response.content == REAL_PNG_BYTES
        assert response.headers.get("content-type", "").startswith("image/png")
        assert "max-age=31536000" in response.headers.get("cache-control", "")
        assert "inline" in response.headers.get("content-disposition", "")
        assert "etag" in response.headers
        assert "last-modified" in response.headers


class TestPredefinedAvatarsIntegration:
    """Tests for getting predefined avatar IDs."""

    @pytest.fixture(autouse=True)
    def setup(
        self,
        api_client: TestClient,
        user_client: UserCreator,
        authenticated_user: CreatedUser,
        auth_headers: dict[str, str],
        db_session: AsyncSession,
        _portal: BlockingPortal,
    ):
        self.client = api_client
        self.user_client = user_client
        self.user = authenticated_user
        self.headers = auth_headers
        self.db = db_session
        self.portal = _portal
        self.predefined_ids = create_predefined_avatars(self.db, self.portal, 3)

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
