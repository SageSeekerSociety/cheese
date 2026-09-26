import mimetypes
from typing import Any, BinaryIO

from app.core.errors import ForbiddenError, InternalServerError, NotFoundError
from app.core.storage import StorageBackend, compute_file_hash, generate_storage_key
from app.domain.attachment.models import Attachment, AttachmentType
from app.domain.attachment.repositories import AttachmentRepository


def detect_attachment_type(content_type: str) -> AttachmentType:
    """Detect AttachmentType from MIME content type."""
    if content_type.startswith("image/"):
        return AttachmentType.IMAGE
    if content_type.startswith("video/"):
        return AttachmentType.VIDEO
    if content_type.startswith("audio/"):
        return AttachmentType.AUDIO
    return AttachmentType.FILE


class AttachmentService:
    def __init__(
        self,
        repo: AttachmentRepository,
        storage: StorageBackend,
    ) -> None:
        self._repo = repo
        self._storage = storage

    async def upload(
        self,
        *,
        file: BinaryIO,
        filename: str,
        content_type: str | None = None,
        uploader_id: int,
        attachment_type: str | None = None,
    ) -> Attachment:
        if not content_type:
            content_type, _ = mimetypes.guess_type(filename)
            content_type = content_type or "application/octet-stream"

        if attachment_type:
            final_type = attachment_type
        else:
            final_type = detect_attachment_type(content_type).value

        import asyncio

        file_content = await asyncio.to_thread(file.read)
        file_size = len(file_content)
        file.seek(0)

        storage_key = generate_storage_key(filename, prefix=f"attachments/{final_type}")
        file_hash = await asyncio.to_thread(compute_file_hash, file)

        url = await self._storage.upload(file, storage_key, content_type)

        meta: dict[str, Any] = {
            "filename": filename,
            "contentType": content_type,
            "storageKey": storage_key,
            "hash": file_hash,
            "uploaderId": uploader_id,
            "size": file_size,
        }

        attachment = await self._repo.create(
            attachment_type=final_type,
            url=url,
            meta=meta,
        )
        return attachment

    async def register_stored(
        self,
        *,
        filename: str,
        content_type: str,
        storage_key: str,
        url: str,
        size: int,
        uploader_id: int,
        file_hash: str,
        attachment_type: str | None = None,
    ) -> Attachment:
        """登记一个**已经躺在存储上**的对象（PDF 导入那条路）。

        ``upload`` 是「文件在调用者手上」那条路：它自己定 key、写对象、算摘要。PDF
        导入不是 —— 抽出的插图在生成草稿那一步就已经写进存储了（题干里的图片链接指
        的就是它），到这里再写一份对象只会让同一张图在存储上存两份，两处地址还不
        一样。所以这里只补那一行记录，用的是与 ``upload`` **同一个 meta 形状**，让
        下游（下载端点、``meta.uploaderId`` 那道校验）看不出两条路的差别。
        """
        final_type = (
            attachment_type
            if attachment_type
            else detect_attachment_type(content_type).value
        )
        meta: dict[str, Any] = {
            "filename": filename,
            "contentType": content_type,
            "storageKey": storage_key,
            "hash": file_hash,
            "uploaderId": uploader_id,
            "size": size,
        }
        return await self._repo.create(
            attachment_type=final_type,
            url=url,
            meta=meta,
        )

    async def get(self, attachment_id: int) -> Attachment:
        attachment = await self._repo.get_by_id(attachment_id)
        if attachment is None:
            raise NotFoundError.for_resource("attachment", attachment_id)
        return attachment

    async def get_many(self, ids: list[int]) -> list[Attachment]:
        return await self._repo.get_by_ids(ids)

    async def download(self, attachment_id: int) -> tuple[bytes, str, str]:
        """Download attachment and return (content, filename, content_type)."""
        attachment = await self.get(attachment_id)
        storage_key = attachment.meta.get("storageKey")
        if not storage_key:
            raise NotFoundError("Attachment storage key not found")

        content = await self._storage.download(storage_key)
        if content is None:
            raise NotFoundError("Attachment file not found in storage")

        filename = attachment.meta.get("filename", f"attachment_{attachment_id}")
        content_type = attachment.meta.get("contentType", "application/octet-stream")
        return content, filename, content_type

    async def delete(self, attachment_id: int, user_id: int) -> None:
        attachment = await self._repo.get_by_id(attachment_id)
        if attachment is None:
            raise NotFoundError.for_resource("attachment", attachment_id)

        uploader_id = attachment.meta.get("uploaderId")
        if uploader_id != user_id:
            raise ForbiddenError("Only the uploader can delete the attachment")

        storage_key = attachment.meta.get("storageKey")
        if storage_key and not await self._storage.delete(storage_key):
            # The row is the only pointer to the object, so dropping it after a
            # failed delete orphans the file for good: it keeps costing storage
            # and stays fetchable by key, while the caller was told 204.
            #
            # A false return is not proof of failure though — the local backend
            # returns it for a file that was already absent, which is the end
            # state we wanted. Ask what is actually there; refusing a legitimate
            # delete because the object had already gone would be its own bug.
            if await self._storage.exists(storage_key):
                raise InternalServerError(
                    f"attachment {attachment_id} was not removed from storage; "
                    "the record is kept so the object can still be found"
                )

        await self._repo.delete(attachment_id)
