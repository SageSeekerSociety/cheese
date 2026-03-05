"""
Integration tests for the Attachments module.
Migrated from cheese-backend/test/attachments.e2e-spec.ts
"""

import io

import httpx
import pytest

from tests.integration.conftest import CreatedUser, UserCreator


class TestAttachmentsIntegration:
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
        self.attachment_ids: list[int] = []

    def _create_aux_user(self) -> tuple[CreatedUser, dict[str, str]]:
        user = self.user_client.create_user()
        token = self.user_client.login(self.client, user.username, user.password)
        user.token = token
        return user, {"Authorization": f"Bearer {token}"}

    def test_upload_image_attachment(self):
        fake_image = io.BytesIO(b"fake image content for attachment test")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "image"},
            files={"file": ("test.jpg", fake_image, "image/jpeg")},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["code"] == 201
        assert "id" in data["data"]
        self.attachment_ids.append(data["data"]["id"])

    def test_upload_image_as_file(self):
        fake_image = io.BytesIO(b"fake image content uploaded as file")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "file"},
            files={"file": ("test.jpg", fake_image, "image/jpeg")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data["data"]

    def test_upload_video_attachment(self):
        fake_video = io.BytesIO(b"fake video content for attachment test")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "video"},
            files={"file": ("test.mp4", fake_video, "video/mp4")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data["data"]

    def test_upload_video_as_file(self):
        fake_video = io.BytesIO(b"fake video content uploaded as file")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "file"},
            files={"file": ("test.mp4", fake_video, "video/mp4")},
        )
        assert response.status_code == 201

    def test_upload_audio_attachment(self):
        fake_audio = io.BytesIO(b"fake audio content for attachment test")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "audio"},
            files={"file": ("test.mp3", fake_audio, "audio/mpeg")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data["data"]

    def test_upload_audio_as_file(self):
        fake_audio = io.BytesIO(b"fake audio content uploaded as file")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "file"},
            files={"file": ("test.mp3", fake_audio, "audio/mpeg")},
        )
        assert response.status_code == 201

    def test_upload_pdf_file(self):
        fake_pdf = io.BytesIO(b"%PDF-1.4 fake pdf content for attachment test")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "file"},
            files={"file": ("test.pdf", fake_pdf, "application/pdf")},
        )
        assert response.status_code == 201
        data = response.json()
        assert "id" in data["data"]

    def test_upload_invalid_type(self):
        fake_file = io.BytesIO(b"fake content")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "invalid_type"},
            files={"file": ("test.pdf", fake_file, "application/pdf")},
        )
        assert response.status_code == 400

    def test_upload_mime_type_mismatch(self):
        fake_pdf = io.BytesIO(b"%PDF-1.4 fake pdf content")
        response = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "image"},
            files={"file": ("test.pdf", fake_pdf, "application/pdf")},
        )
        assert response.status_code == 422

    def test_get_attachment(self):
        fake_image = io.BytesIO(b"fake image content for get test")
        upload_resp = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "image"},
            files={"file": ("test.jpg", fake_image, "image/jpeg")},
        )
        attachment_id = upload_resp.json()["data"]["id"]

        response = self.client.get(
            f"/attachments/{attachment_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        data = response.json()
        assert "attachment" in data["data"]
        assert data["data"]["attachment"]["id"] == attachment_id

    def test_get_attachment_not_found(self):
        response = self.client.get(
            "/attachments/999999999",
            headers=self.headers,
        )
        assert response.status_code == 404

    def test_get_attachment_metadata(self):
        fake_file = io.BytesIO(b"%PDF-1.4 fake pdf with metadata")
        upload_resp = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "file"},
            files={"file": ("test.pdf", fake_file, "application/pdf")},
        )
        attachment_id = upload_resp.json()["data"]["id"]

        response = self.client.get(
            f"/attachments/{attachment_id}",
            headers=self.headers,
        )
        assert response.status_code == 200
        attachment = response.json()["data"]["attachment"]
        assert "meta" in attachment
        assert "size" in attachment["meta"]

    def test_upload_no_auth(self):
        fake_file = io.BytesIO(b"fake content")
        response = self.client.post(
            "/attachments",
            data={"type": "file"},
            files={"file": ("test.pdf", fake_file, "application/pdf")},
        )
        assert response.status_code == 401

    def test_delete_attachment(self):
        fake_image = io.BytesIO(b"fake image content for delete test")
        upload_resp = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "image"},
            files={"file": ("test.jpg", fake_image, "image/jpeg")},
        )
        attachment_id = upload_resp.json()["data"]["id"]

        response = self.client.delete(
            f"/attachments/{attachment_id}",
            headers=self.headers,
        )
        assert response.status_code in (200, 204)

        get_resp = self.client.get(
            f"/attachments/{attachment_id}",
            headers=self.headers,
        )
        assert get_resp.status_code == 404

    def test_delete_attachment_not_owner(self):
        fake_image = io.BytesIO(b"fake image content for owner test")
        upload_resp = self.client.post(
            "/attachments",
            headers=self.headers,
            data={"type": "image"},
            files={"file": ("test.jpg", fake_image, "image/jpeg")},
        )
        attachment_id = upload_resp.json()["data"]["id"]

        aux_user, aux_headers = self._create_aux_user()
        response = self.client.delete(
            f"/attachments/{attachment_id}",
            headers=aux_headers,
        )
        assert response.status_code == 403

    def test_delete_attachment_not_found(self):
        response = self.client.delete(
            "/attachments/999999999",
            headers=self.headers,
        )
        assert response.status_code == 404
