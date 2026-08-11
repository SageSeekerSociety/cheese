"""Unit tests for AttachmentService and detect_attachment_type."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.attachment.models import AttachmentType
from app.domain.attachment.services import AttachmentService, detect_attachment_type

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_attachment(**overrides):
    defaults = {
        "id": 1,
        "type": "image",
        "url": "https://storage.example.com/attachments/image/2024/01/01/abc123.png",
        "meta": {
            "filename": "photo.png",
            "contentType": "image/png",
            "storageKey": "attachments/image/2024/01/01/abc123.png",
            "hash": "d41d8cd98f00b204e9800998ecf8427e",
            "uploaderId": 42,
            "size": 1024,
        },
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def _make_service(
    *,
    repo: AsyncMock | None = None,
    storage: AsyncMock | None = None,
) -> AttachmentService:
    if repo is None:
        repo = AsyncMock()
    if storage is None:
        storage = AsyncMock()
    return AttachmentService(repo=repo, storage=storage)


# ---------------------------------------------------------------------------
# detect_attachment_type (module-level function)
# ---------------------------------------------------------------------------


class TestDetectAttachmentType:
    def test_image_types(self):
        assert detect_attachment_type("image/png") == AttachmentType.IMAGE
        assert detect_attachment_type("image/jpeg") == AttachmentType.IMAGE
        assert detect_attachment_type("image/gif") == AttachmentType.IMAGE
        assert detect_attachment_type("image/webp") == AttachmentType.IMAGE

    def test_video_types(self):
        assert detect_attachment_type("video/mp4") == AttachmentType.VIDEO
        assert detect_attachment_type("video/webm") == AttachmentType.VIDEO

    def test_audio_types(self):
        assert detect_attachment_type("audio/mpeg") == AttachmentType.AUDIO
        assert detect_attachment_type("audio/ogg") == AttachmentType.AUDIO

    def test_fallback_to_file(self):
        assert detect_attachment_type("application/pdf") == AttachmentType.FILE
        assert detect_attachment_type("text/plain") == AttachmentType.FILE
        assert detect_attachment_type("application/octet-stream") == AttachmentType.FILE


# ---------------------------------------------------------------------------
# upload
# ---------------------------------------------------------------------------


class TestUpload:
    @pytest.mark.anyio
    async def test_upload_with_explicit_content_type(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment()
        repo.create.return_value = attachment
        storage.upload.return_value = "https://storage.example.com/key"
        svc = _make_service(repo=repo, storage=storage)

        file = io.BytesIO(b"file-content")

        with (
            patch(
                "app.domain.attachment.services.generate_storage_key",
                return_value="attachments/image/2024/01/01/abc123.png",
            ),
            patch(
                "app.domain.attachment.services.compute_file_hash",
                return_value="fakehash",
            ),
        ):
            result = await svc.upload(
                file=file,
                filename="photo.png",
                content_type="image/png",
                uploader_id=42,
            )

        assert result is attachment
        storage.upload.assert_awaited_once()
        repo.create.assert_awaited_once()
        call_kw = repo.create.call_args.kwargs
        assert call_kw["attachment_type"] == "image"
        assert call_kw["url"] == "https://storage.example.com/key"
        meta = call_kw["meta"]
        assert meta["filename"] == "photo.png"
        assert meta["contentType"] == "image/png"
        assert meta["storageKey"] == "attachments/image/2024/01/01/abc123.png"
        assert meta["hash"] == "fakehash"
        assert meta["uploaderId"] == 42
        assert meta["size"] == len(b"file-content")

    @pytest.mark.anyio
    async def test_upload_guesses_content_type_when_none(self):
        repo = AsyncMock()
        storage = AsyncMock()
        repo.create.return_value = _make_attachment()
        storage.upload.return_value = "https://storage.example.com/key"
        svc = _make_service(repo=repo, storage=storage)

        file = io.BytesIO(b"data")

        with (
            patch(
                "app.domain.attachment.services.generate_storage_key",
                return_value="key",
            ),
            patch(
                "app.domain.attachment.services.compute_file_hash",
                return_value="hash",
            ),
        ):
            await svc.upload(
                file=file,
                filename="doc.pdf",
                content_type=None,
                uploader_id=1,
            )

        call_kw = repo.create.call_args.kwargs
        assert call_kw["meta"]["contentType"] == "application/pdf"
        assert call_kw["attachment_type"] == "file"

    @pytest.mark.anyio
    async def test_upload_falls_back_to_octet_stream_for_unknown_extension(self):
        repo = AsyncMock()
        storage = AsyncMock()
        repo.create.return_value = _make_attachment()
        storage.upload.return_value = "url"
        svc = _make_service(repo=repo, storage=storage)

        file = io.BytesIO(b"data")

        with (
            patch(
                "app.domain.attachment.services.generate_storage_key",
                return_value="key",
            ),
            patch(
                "app.domain.attachment.services.compute_file_hash",
                return_value="hash",
            ),
            patch("mimetypes.guess_type", return_value=(None, None)),
        ):
            await svc.upload(
                file=file,
                filename="weird.xyz123",
                content_type=None,
                uploader_id=1,
            )

        call_kw = repo.create.call_args.kwargs
        assert call_kw["meta"]["contentType"] == "application/octet-stream"
        assert call_kw["attachment_type"] == "file"

    @pytest.mark.anyio
    async def test_upload_empty_content_type_string_triggers_guessing(self):
        repo = AsyncMock()
        storage = AsyncMock()
        repo.create.return_value = _make_attachment()
        storage.upload.return_value = "url"
        svc = _make_service(repo=repo, storage=storage)

        file = io.BytesIO(b"data")

        with (
            patch(
                "app.domain.attachment.services.generate_storage_key",
                return_value="key",
            ),
            patch(
                "app.domain.attachment.services.compute_file_hash",
                return_value="hash",
            ),
        ):
            await svc.upload(
                file=file,
                filename="image.jpg",
                content_type="",
                uploader_id=1,
            )

        call_kw = repo.create.call_args.kwargs
        assert call_kw["meta"]["contentType"] == "image/jpeg"
        assert call_kw["attachment_type"] == "image"

    @pytest.mark.anyio
    async def test_upload_with_explicit_attachment_type_overrides_detection(self):
        repo = AsyncMock()
        storage = AsyncMock()
        repo.create.return_value = _make_attachment()
        storage.upload.return_value = "url"
        svc = _make_service(repo=repo, storage=storage)

        file = io.BytesIO(b"data")

        with (
            patch(
                "app.domain.attachment.services.generate_storage_key",
                return_value="key",
            ),
            patch(
                "app.domain.attachment.services.compute_file_hash",
                return_value="hash",
            ),
        ):
            await svc.upload(
                file=file,
                filename="photo.png",
                content_type="image/png",
                uploader_id=1,
                attachment_type="custom_type",
            )

        call_kw = repo.create.call_args.kwargs
        assert call_kw["attachment_type"] == "custom_type"

    @pytest.mark.anyio
    async def test_upload_reads_file_size_correctly(self):
        repo = AsyncMock()
        storage = AsyncMock()
        repo.create.return_value = _make_attachment()
        storage.upload.return_value = "url"
        svc = _make_service(repo=repo, storage=storage)

        content = b"x" * 5000
        file = io.BytesIO(content)

        with (
            patch(
                "app.domain.attachment.services.generate_storage_key",
                return_value="key",
            ),
            patch(
                "app.domain.attachment.services.compute_file_hash",
                return_value="hash",
            ),
        ):
            await svc.upload(
                file=file,
                filename="big.bin",
                content_type="application/octet-stream",
                uploader_id=1,
            )

        meta = repo.create.call_args.kwargs["meta"]
        assert meta["size"] == 5000


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------


class TestGet:
    @pytest.mark.anyio
    async def test_returns_attachment_when_found(self):
        repo = AsyncMock()
        attachment = _make_attachment()
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo)

        result = await svc.get(1)

        assert result is attachment
        repo.get_by_id.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_raises_not_found_when_missing(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError):
            await svc.get(999)


# ---------------------------------------------------------------------------
# get_many
# ---------------------------------------------------------------------------


class TestGetMany:
    @pytest.mark.anyio
    async def test_returns_list_from_repo(self):
        repo = AsyncMock()
        attachments = [_make_attachment(id=1), _make_attachment(id=2)]
        repo.get_by_ids.return_value = attachments
        svc = _make_service(repo=repo)

        result = await svc.get_many([1, 2])

        assert result == attachments
        repo.get_by_ids.assert_awaited_once_with([1, 2])

    @pytest.mark.anyio
    async def test_returns_empty_list_for_empty_ids(self):
        repo = AsyncMock()
        repo.get_by_ids.return_value = []
        svc = _make_service(repo=repo)

        result = await svc.get_many([])

        assert result == []


# ---------------------------------------------------------------------------
# download
# ---------------------------------------------------------------------------


class TestDownload:
    @pytest.mark.anyio
    async def test_downloads_attachment_successfully(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment()
        repo.get_by_id.return_value = attachment
        storage.download.return_value = b"file-bytes"
        svc = _make_service(repo=repo, storage=storage)

        content, filename, content_type = await svc.download(1)

        assert content == b"file-bytes"
        assert filename == "photo.png"
        assert content_type == "image/png"
        storage.download.assert_awaited_once_with(
            "attachments/image/2024/01/01/abc123.png"
        )

    @pytest.mark.anyio
    async def test_raises_not_found_when_attachment_missing(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError):
            await svc.download(999)

    @pytest.mark.anyio
    async def test_raises_not_found_when_storage_key_missing(self):
        repo = AsyncMock()
        attachment = _make_attachment(meta={})
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError, match="storage key not found"):
            await svc.download(1)

    @pytest.mark.anyio
    async def test_raises_not_found_when_file_missing_in_storage(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment()
        repo.get_by_id.return_value = attachment
        storage.download.return_value = None
        svc = _make_service(repo=repo, storage=storage)

        with pytest.raises(NotFoundError, match="file not found in storage"):
            await svc.download(1)

    @pytest.mark.anyio
    async def test_uses_default_filename_and_content_type_when_missing(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment(meta={"storageKey": "some/key"})
        repo.get_by_id.return_value = attachment
        storage.download.return_value = b"data"
        svc = _make_service(repo=repo, storage=storage)

        content, filename, content_type = await svc.download(1)

        assert content == b"data"
        assert filename == "attachment_1"
        assert content_type == "application/octet-stream"

    @pytest.mark.anyio
    async def test_raises_when_storage_key_is_empty_string(self):
        """An empty string for storageKey is falsy, so treated as missing."""
        repo = AsyncMock()
        attachment = _make_attachment(meta={"storageKey": ""})
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError, match="storage key not found"):
            await svc.download(1)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------


class TestDelete:
    @pytest.mark.anyio
    async def test_deletes_attachment_by_uploader(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment()
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo, storage=storage)

        await svc.delete(attachment_id=1, user_id=42)

        storage.delete.assert_awaited_once_with(
            "attachments/image/2024/01/01/abc123.png"
        )
        repo.delete.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_raises_not_found_when_attachment_missing(self):
        repo = AsyncMock()
        repo.get_by_id.return_value = None
        svc = _make_service(repo=repo)

        with pytest.raises(NotFoundError):
            await svc.delete(attachment_id=999, user_id=42)

    @pytest.mark.anyio
    async def test_raises_forbidden_when_not_uploader(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment()  # uploaderId=42
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo, storage=storage)

        with pytest.raises(ForbiddenError, match="Only the uploader"):
            await svc.delete(attachment_id=1, user_id=99)

        storage.delete.assert_not_awaited()
        repo.delete.assert_not_awaited()

    @pytest.mark.anyio
    async def test_skips_storage_delete_when_no_storage_key(self):
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment(meta={"uploaderId": 42})
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo, storage=storage)

        await svc.delete(attachment_id=1, user_id=42)

        storage.delete.assert_not_awaited()
        repo.delete.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_skips_storage_delete_when_storage_key_is_empty(self):
        """An empty string for storageKey is falsy, so storage.delete is skipped."""
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment(meta={"uploaderId": 42, "storageKey": ""})
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo, storage=storage)

        await svc.delete(attachment_id=1, user_id=42)

        storage.delete.assert_not_awaited()
        repo.delete.assert_awaited_once_with(1)

    @pytest.mark.anyio
    async def test_raises_forbidden_when_uploader_id_missing_from_meta(self):
        """When uploaderId is missing from meta, it's None != user_id."""
        repo = AsyncMock()
        storage = AsyncMock()
        attachment = _make_attachment(meta={"storageKey": "k"})
        repo.get_by_id.return_value = attachment
        svc = _make_service(repo=repo, storage=storage)

        with pytest.raises(ForbiddenError, match="Only the uploader"):
            await svc.delete(attachment_id=1, user_id=42)
