"""
Integration tests for the Materials and MaterialBundle modules.
Migrated from:
- cheese-backend/test/materials.e2e-spec.ts (236 lines, 14 tests)
- cheese-backend/test/materialbundle.e2e-spec.ts (493 lines, 23 tests)
Complete equivalence migration (37 tests total).
"""

import io
import random

import httpx
import pytest

from tests.integration.conftest import CreatedUser, UserCreator


class TestMaterialsUploadIntegration:
    """Tests for uploading materials."""

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

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_upload_image_material(self):
        fake_file = io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"fake image content")
        response = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.jpg", fake_file, "image/jpeg")},
            data={"type": "image"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_upload_video_material(self):
        fake_file = io.BytesIO(b"fake video content for testing")
        response = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.mp4", fake_file, "video/mp4")},
            data={"type": "video"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_upload_audio_material(self):
        fake_file = io.BytesIO(b"fake audio content for testing")
        response = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.mp3", fake_file, "audio/mpeg")},
            data={"type": "audio"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_upload_file_material(self):
        fake_file = io.BytesIO(b"fake pdf content for testing")
        response = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.pdf", fake_file, "application/pdf")},
            data={"type": "file"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_get_material(self):
        fake_file = io.BytesIO(b"get material test content")
        upload_resp = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("get_test.pdf", fake_file, "application/pdf")},
            data={"type": "file"},
        )
        material_id = upload_resp.json()["data"]["id"]

        response = self.client.get(f"/materials/{material_id}", headers=self.headers)
        assert response.status_code == 200
        material = response.json()["data"]["material"]
        assert material["id"] == material_id

    def test_get_material_not_found(self):
        response = self.client.get("/materials/999999999", headers=self.headers)
        assert response.status_code == 404

    def test_delete_material(self):
        fake_file = io.BytesIO(b"delete test content")
        upload_resp = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("delete_test.pdf", fake_file, "application/pdf")},
            data={"type": "file"},
        )
        material_id = upload_resp.json()["data"]["id"]

        response = self.client.delete(f"/materials/{material_id}", headers=self.headers)
        assert response.status_code in (200, 204)

        get_resp = self.client.get(f"/materials/{material_id}", headers=self.headers)
        assert get_resp.status_code == 404

    def test_delete_material_not_owner(self):
        fake_file = io.BytesIO(b"owner only delete content")
        upload_resp = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("owner_test.pdf", fake_file, "application/pdf")},
            data={"type": "file"},
        )
        material_id = upload_resp.json()["data"]["id"]

        aux_user, aux_headers = self._create_aux_user()
        response = self.client.delete(f"/materials/{material_id}", headers=aux_headers)
        assert response.status_code == 403

    def test_upload_invalid_type(self):
        fake_file = io.BytesIO(b"fake content for invalid type")
        response = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.jpg", fake_file, "image/jpeg")},
            data={"type": "invalid_type"},
        )
        assert response.status_code == 400

    def test_upload_mime_type_mismatch(self):
        fake_pdf = io.BytesIO(b"%PDF-1.4 fake pdf content")
        response = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("test.pdf", fake_pdf, "application/pdf")},
            data={"type": "image"},
        )
        assert response.status_code == 422

    def test_upload_material_no_auth(self):
        fake_file = io.BytesIO(b"no auth test content")
        response = self.client.post(
            "/materials",
            files={"file": ("test.pdf", fake_file, "application/pdf")},
            data={"type": "file"},
        )
        assert response.status_code == 401

    def test_get_material_no_auth(self):
        fake_file = io.BytesIO(b"get material no auth content")
        upload_resp = self.client.post(
            "/materials",
            headers=self.headers,
            files={"file": ("noauth_test.pdf", fake_file, "application/pdf")},
            data={"type": "file"},
        )
        material_id = upload_resp.json()["data"]["id"]

        response = self.client.get(f"/materials/{material_id}")
        assert response.status_code == 401


class TestMaterialBundlesCreateIntegration:
    """Tests for creating material bundles."""

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
        self.unique = str(random.randint(1000000000, 9999999999))
        self.material_ids: list[int] = []
        for mat_type, content_type, filename in [
            ("image", "image/jpeg", "test.jpg"),
            ("video", "video/mp4", "test.mp4"),
            ("audio", "audio/mpeg", "test.mp3"),
            ("file", "application/pdf", "test.pdf"),
        ]:
            fake_file = io.BytesIO(f"fake {mat_type} content".encode())
            resp = self.client.post(
                "/materials",
                headers=self.headers,
                files={"file": (filename, fake_file, content_type)},
                data={"type": mat_type},
            )
            self.material_ids.append(resp.json()["data"]["id"])
        self.image_id, self.video_id, self.audio_id, self.file_id = self.material_ids

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_create_bundle_with_materials(self):
        response = self.client.post(
            "/material-bundles",
            headers=self.headers,
            json={
                "title": f"Bundle_{self.unique}_1",
                "content": "content about materialbundle",
                "materials": [self.image_id, self.video_id, self.audio_id],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_create_bundle_without_materials(self):
        response = self.client.post(
            "/material-bundles",
            headers=self.headers,
            json={
                "title": f"Bundle_{self.unique}_empty",
                "content": "bundle without materials",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]

    def test_create_bundle_material_not_found(self):
        response = self.client.post(
            "/material-bundles",
            headers=self.headers,
            json={
                "title": f"Bundle_{self.unique}_bad",
                "content": "content",
                "materials": [self.image_id, self.video_id, self.file_id, self.file_id + 30],
            },
        )
        assert response.status_code == 404

    def test_create_bundle_no_auth(self):
        response = self.client.post(
            "/material-bundles",
            json={
                "title": f"Bundle_{self.unique}_noauth",
                "content": "content",
                "materials": [self.image_id],
            },
        )
        assert response.status_code == 401


class TestMaterialBundlesGetIntegration:
    """Tests for getting material bundles with pagination."""

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
        self.unique = str(random.randint(1000000000, 9999999999))
        self.material_ids: list[int] = []
        for mat_type, content_type, filename in [
            ("image", "image/jpeg", "test.jpg"),
            ("video", "video/mp4", "test.mp4"),
            ("audio", "audio/mpeg", "test.mp3"),
        ]:
            fake_file = io.BytesIO(f"fake {mat_type} content".encode())
            resp = self.client.post(
                "/materials",
                headers=self.headers,
                files={"file": (filename, fake_file, content_type)},
                data={"type": mat_type},
            )
            self.material_ids.append(resp.json()["data"]["id"])
        self.image_id, self.video_id, self.audio_id = self.material_ids
        resp = self.client.post(
            "/material-bundles",
            headers=self.headers,
            json={
                "title": "a materialbundle",
                "content": "content about materialbundle",
                "materials": [self.image_id, self.video_id, self.audio_id],
            },
        )
        self.bundle_id1 = resp.json()["data"]["id"]
        self.bundle_ids: list[int] = []
        for _i in range(20):
            resp = self.client.post(
                "/material-bundles",
                headers=self.headers,
                json={
                    "title": f"test_for_pagination-{self.unique}",
                    "content": "just_for_test",
                },
            )
            self.bundle_ids.append(resp.json()["data"]["id"])

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_get_all_bundles(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={"q": "", "sort": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert data["data"]["page"]["page_size"] == 20
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is True

    def test_get_bundles_with_keyword(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={"q": self.unique, "sort": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["materials"]) == 20
        for i, material in enumerate(data["data"]["materials"][:20]):
            assert material["id"] == self.bundle_ids[i]
        assert data["data"]["page"]["page_size"] == 20
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is False
        assert data["data"]["page"]["next_start"] == 0

    def test_get_bundles_with_keyword_and_size(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={"q": self.unique, "page_size": 10, "sort": ""},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["materials"]) == 10
        for i, material in enumerate(data["data"]["materials"][:10]):
            assert material["id"] == self.bundle_ids[i]
        assert data["data"]["page"]["page_size"] == 10
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.bundle_ids[10]

    def test_get_bundles_with_keyword_size_and_start(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={
                "q": self.unique,
                "page_size": 10,
                "page_start": self.bundle_ids[4],
                "sort": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["materials"]) == 10
        for i, material in enumerate(data["data"]["materials"][:10]):
            assert material["id"] == self.bundle_ids[i + 4]
        assert data["data"]["page"]["page_size"] == 10
        assert data["data"]["page"]["has_prev"] is True
        assert data["data"]["page"]["prev_start"] == self.bundle_ids[3]
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.bundle_ids[14]

    def test_get_bundles_with_search_syntax(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={
                "q": f"title:{self.unique} id:>={self.bundle_ids[4]}",
                "page_size": 10,
                "sort": "",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["materials"]) == 10
        for i, material in enumerate(data["data"]["materials"][:10]):
            assert material["id"] == self.bundle_ids[i + 4]
        assert data["data"]["page"]["page_size"] == 10
        assert data["data"]["page"]["has_prev"] is False
        assert data["data"]["page"]["prev_start"] == 0
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.bundle_ids[14]

    def test_get_bundles_with_sort_newest(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={
                "q": self.unique,
                "page_size": 10,
                "page_start": self.bundle_ids[14],
                "sort": "newest",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["code"] == 200
        assert len(data["data"]["materials"]) == 10
        for i, material in enumerate(data["data"]["materials"][:10]):
            assert material["id"] == self.bundle_ids[14 - i]
        assert data["data"]["page"]["page_size"] == 10
        assert data["data"]["page"]["has_prev"] is True
        assert data["data"]["page"]["prev_start"] == self.bundle_ids[15]
        assert data["data"]["page"]["has_more"] is True
        assert data["data"]["page"]["next_start"] == self.bundle_ids[4]

    def test_get_bundles_keyword_too_long(self):
        response = self.client.get(
            "/material-bundles",
            headers=self.headers,
            params={
                "q": "yui" * 100,
                "page_size": 10,
                "page_start": self.bundle_ids[14],
                "sort": "newest",
            },
        )
        assert response.status_code == 400

    def test_get_bundle_detail(self):
        response = self.client.get(
            f"/material-bundles/{self.bundle_id1}",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        bundle = data["data"]["materialBundle"]
        assert bundle["title"] == "a materialbundle"
        assert bundle["content"] == "content about materialbundle"
        assert bundle["creator"]["id"] == self.user.user_id
        assert len(bundle["materials"]) == 3
        for material in bundle["materials"]:
            assert material["id"] in [self.image_id, self.video_id, self.audio_id]

    def test_get_bundle_not_found(self):
        response = self.client.get(
            f"/material-bundles/{self.bundle_id1 + 30}",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_bundle_no_auth(self):
        response = self.client.get(f"/material-bundles/{self.bundle_id1}")
        assert response.status_code == 401


class TestMaterialBundlesUpdateIntegration:
    """Tests for updating material bundles."""

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
        self.unique = str(random.randint(1000000000, 9999999999))
        self.material_ids: list[int] = []
        for mat_type, content_type, filename in [
            ("image", "image/jpeg", "test.jpg"),
            ("video", "video/mp4", "test.mp4"),
            ("audio", "audio/mpeg", "test.mp3"),
            ("file", "application/pdf", "test.pdf"),
        ]:
            fake_file = io.BytesIO(f"fake {mat_type} content".encode())
            resp = self.client.post(
                "/materials",
                headers=self.headers,
                files={"file": (filename, fake_file, content_type)},
                data={"type": mat_type},
            )
            self.material_ids.append(resp.json()["data"]["id"])
        self.image_id, self.video_id, self.audio_id, self.file_id = self.material_ids
        resp = self.client.post(
            "/material-bundles",
            headers=self.headers,
            json={
                "title": f"Bundle_{self.unique}_update",
                "content": "content about materialbundle",
                "materials": [self.image_id, self.video_id, self.file_id],
            },
        )
        self.bundle_id = resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_update_bundle(self):
        response = self.client.patch(
            f"/material-bundles/{self.bundle_id}",
            headers=self.headers,
            json={
                "title": "new title",
                "content": "new content",
                "materials": [self.image_id, self.video_id, self.audio_id],
            },
        )
        assert response.status_code == 200

        get_resp = self.client.get(
            f"/material-bundles/{self.bundle_id}",
            headers=self.headers,
        )
        bundle = get_resp.json()["data"]["materialBundle"]
        assert bundle["title"] == "new title"
        assert bundle["content"] == "new content"
        assert bundle["creator"]["id"] == self.user.user_id
        assert len(bundle["materials"]) == 3
        for material in bundle["materials"]:
            assert material["id"] in [self.image_id, self.video_id, self.audio_id]

    def test_update_bundle_not_found(self):
        response = self.client.patch(
            f"/material-bundles/{self.bundle_id + 30}",
            headers=self.headers,
            json={"title": "new title"},
        )
        assert response.status_code == 404

    def test_update_bundle_not_owner(self):
        response = self.client.patch(
            f"/material-bundles/{self.bundle_id}",
            headers=self.aux_headers,
            json={"title": "hacked"},
        )
        assert response.status_code == 403

    def test_update_bundle_no_auth(self):
        response = self.client.patch(
            f"/material-bundles/{self.bundle_id}",
            json={"title": "hacked"},
        )
        assert response.status_code == 401


class TestMaterialBundlesDeleteIntegration:
    """Tests for deleting material bundles."""

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
        self.unique = str(random.randint(1000000000, 9999999999))
        resp = self.client.post(
            "/material-bundles",
            headers=self.headers,
            json={
                "title": f"Bundle_{self.unique}_delete",
                "content": "content to delete",
            },
        )
        self.bundle_id = resp.json()["data"]["id"]
        self.aux_user, self.aux_headers = self._create_aux_user()

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_delete_bundle_no_auth(self):
        response = self.client.delete(f"/material-bundles/{self.bundle_id}")
        assert response.status_code == 401

    def test_delete_bundle_not_found(self):
        response = self.client.delete(
            f"/material-bundles/{self.bundle_id + 30}",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_delete_bundle_not_owner(self):
        response = self.client.delete(
            f"/material-bundles/{self.bundle_id}",
            headers=self.aux_headers,
        )
        assert response.status_code == 403

    def test_delete_bundle(self):
        response = self.client.delete(
            f"/material-bundles/{self.bundle_id}",
            headers=self.headers,
        )
        assert response.status_code in (200, 204)

        get_resp = self.client.get(
            f"/material-bundles/{self.bundle_id}",
            headers=self.headers,
        )
        assert get_resp.status_code == 404
