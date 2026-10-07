import asyncio
import hashlib
import io
import logging
import uuid
from abc import ABC, abstractmethod
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, BinaryIO

import aiofiles
import aiofiles.os
import aiofiles.ospath

from app.core.config import settings


@dataclass
class _S3Connections:
    clients: dict[tuple, Any] = field(default_factory=dict)
    stack: AsyncExitStack = field(default_factory=AsyncExitStack)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_s3_connections: _S3Connections | None = None


def _retry_incomplete_s3_upload(response, attempts: int, **_) -> float | None:
    # S3 can reject a truncated upload with HTTP 400, outside SDK default retries.
    if (
        response is not None
        and response[1].get("Error", {}).get("Code") == "IncompleteBody"
        and attempts < 3
    ):
        logging.getLogger(__name__).warning(
            "Retrying incomplete S3 upload after attempt %s", attempts
        )
        return float(attempts)
    return None


@asynccontextmanager
async def reuse_s3_connections():
    """Load SDK models before serving requests and close clients at shutdown."""
    global _s3_connections
    previous = _s3_connections
    pool = _S3Connections()
    _s3_connections = pool
    try:
        async with pool.stack:
            if settings.storage_type == "s3" or (
                settings.s3_endpoint_url
                and settings.s3_access_key
                and settings.s3_secret_key
                and settings.transcript_s3_bucket
            ):
                backend = S3StorageBackend(
                    bucket=settings.s3_bucket,
                    endpoint_url=settings.s3_endpoint_url,
                    access_key=settings.s3_access_key,
                    secret_key=settings.s3_secret_key,
                    region=settings.s3_region,
                )
                async with backend._get_client():
                    pass
            yield
    finally:
        _s3_connections = previous


def _object_is_absent(exc: Exception) -> bool:
    """True when the bucket answered "no such key", rather than not answering.

    Every other failure — a refused connection, a 403 from a rotated key, a 500
    from the gateway — means we do not know what is in the bucket. Reporting
    that as the same absence a genuinely missing object produces is what lets a
    caller act on it: `AttachmentService.delete` asks `exists` precisely to
    avoid dropping the only pointer to an object that is still there, and a
    swallowed connection error answers "gone" to that question.

    Duck-typed on botocore's `ClientError` shape so this module keeps its lazy
    `aioboto3` import; anything without that shape is not an absence.
    """
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    error = response.get("Error")
    if not isinstance(error, dict):
        return False
    return str(error.get("Code", "")) in {"404", "NoSuchKey", "NotFound"}


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, file: BinaryIO, key: str, content_type: str) -> str:
        """Upload file and return the URL/path."""

    @abstractmethod
    async def download(self, key: str) -> bytes | None:
        """Download file content by key."""

    @abstractmethod
    async def delete(self, key: str) -> bool:
        """Delete file by key."""

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """Check if file exists."""

    @abstractmethod
    def get_url(self, key: str) -> str:
        """Get public URL for the file."""


class LocalStorageBackend(StorageBackend):
    def __init__(self, base_path: str, base_url: str) -> None:
        self._base_path = Path(base_path)
        self._base_url = base_url.rstrip("/")
        self._base_path.mkdir(parents=True, exist_ok=True)

    async def upload(self, file: BinaryIO, key: str, content_type: str) -> str:
        file_path = self._base_path / key
        await aiofiles.os.makedirs(str(file_path.parent), exist_ok=True)
        content = await asyncio.to_thread(file.read)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)
        return self.get_url(key)

    async def download(self, key: str) -> bytes | None:
        file_path = self._base_path / key
        if not await aiofiles.ospath.exists(file_path):
            return None
        async with aiofiles.open(file_path, "rb") as f:
            return await f.read()

    async def delete(self, key: str) -> bool:
        file_path = self._base_path / key
        if await aiofiles.ospath.exists(file_path):
            await aiofiles.os.remove(file_path)
            return True
        return False

    async def exists(self, key: str) -> bool:
        return await aiofiles.ospath.exists(self._base_path / key)

    def get_url(self, key: str) -> str:
        return f"{self._base_url}/{key}"


class S3StorageBackend(StorageBackend):
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        public_url: str | None = None,
        read_timeout_s: float = 15,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._public_url = public_url
        self._read_timeout_s = read_timeout_s

    @asynccontextmanager
    async def _get_client(self):  # type: ignore[override]
        import aioboto3
        from aiobotocore.config import AioConfig

        kwargs = dict(
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
            # Short socket timeouts, so a stalled socket is retried well within
            # a caller's transfer deadline.
            config=AioConfig(
                connect_timeout=5,
                read_timeout=self._read_timeout_s,
                retries={"mode": "standard", "total_max_attempts": 3},
            ),
        )

        def new_client() -> Any:
            # aioboto3 inherits boto3's synchronous client typing.
            session: Any = aioboto3.Session()
            session.events.register(
                "needs-retry.s3.UploadPart", _retry_incomplete_s3_upload
            )
            session.events.register(
                "needs-retry.s3.PutObject", _retry_incomplete_s3_upload
            )
            return session.client("s3", **kwargs)

        pool = _s3_connections
        # Standalone callers own one operation; the application owns its pool.
        if pool is None:
            async with new_client() as client:
                yield client
            return
        # Bucket is per operation. Connections must not cross credentials or endpoints.
        key = (self._endpoint_url, self._access_key, self._secret_key, self._region)
        async with pool.lock:
            client = pool.clients.get(key)
            if client is None:
                client = await pool.stack.enter_async_context(new_client())
                pool.clients[key] = client
        yield client

    async def upload(self, file: BinaryIO, key: str, content_type: str) -> str:
        async with self._get_client() as client:
            await client.upload_fileobj(
                file,
                self._bucket,
                key,
                ExtraArgs={"ContentType": content_type},
            )
        return self.get_url(key)

    # The three below answer "absent" only for an object the bucket says is not
    # there. Anything else raises, which is what the local backend already does
    # and what every caller here is written against: `None` and `False` are
    # answers about the object, not about whether we could reach the bucket.

    async def download(self, key: str) -> bytes | None:
        async with self._get_client() as client:
            buffer = io.BytesIO()
            try:
                await client.download_fileobj(self._bucket, key, buffer)
            except Exception as exc:
                if _object_is_absent(exc):
                    return None
                raise
            return buffer.getvalue()

    async def delete(self, key: str) -> bool:
        async with self._get_client() as client:
            try:
                await client.delete_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _object_is_absent(exc):
                    return False
                raise
            return True

    async def exists(self, key: str) -> bool:
        async with self._get_client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _object_is_absent(exc):
                    return False
                raise
            return True

    async def presign(self, key: str, operation: str, expires_s: int) -> str:
        """A URL that lets whoever holds it do one thing to one object for a
        while (``get_object``), with no credential of ours:
        a machine that moves a large file to or from the bucket is handed this
        rather than the key, and the bytes do not pass through the backend."""
        async with self._get_client() as client:
            return await client.generate_presigned_url(
                operation,
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_s,
            )

    async def multipart_upload(self, key: str, content_type: str) -> tuple[str, bool]:
        """The id of the multipart upload in progress to ``key``, started if
        there is none, and whether it was started now: whoever comes back to
        an object it was sending goes on with the parts the bucket already
        holds. Of several, the one listed first, so that two callers settle on
        the same one. R2 spells the id of one upload differently each time it
        lists it, so the id says which upload it is to the bucket alone."""
        async with self._get_client() as client:
            listed = await client.list_multipart_uploads(
                Bucket=self._bucket, Prefix=key
            )
            for upload in listed.get("Uploads", []):
                if upload["Key"] == key:
                    return str(upload["UploadId"]), False
            started = await client.create_multipart_upload(
                Bucket=self._bucket, Key=key, ContentType=content_type
            )
            return str(started["UploadId"]), True

    async def presign_parts(
        self, key: str, upload_id: str, count: int, expires_s: int
    ) -> list[str]:
        """URLs to PUT parts 1 to ``count`` of ``upload_id`` with, as
        ``presign`` is for a whole object: each part is signed on its own."""
        async with self._get_client() as client:
            return [
                await client.generate_presigned_url(
                    "upload_part",
                    Params={
                        "Bucket": self._bucket,
                        "Key": key,
                        "UploadId": upload_id,
                        "PartNumber": number,
                    },
                    ExpiresIn=expires_s,
                )
                for number in range(1, count + 1)
            ]

    async def complete_multipart(
        self, key: str, upload_id: str, etags: list[str]
    ) -> None:
        """Join the parts, in order, given each part's ETag as the bucket
        answered it."""
        async with self._get_client() as client:
            await client.complete_multipart_upload(
                Bucket=self._bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [
                        {"PartNumber": number, "ETag": etag}
                        for number, etag in enumerate(etags, start=1)
                    ]
                },
            )

    async def abort_multipart(self, key: str) -> None:
        """Drop every unfinished upload to ``key`` and the parts it holds."""
        async with self._get_client() as client:
            listed = await client.list_multipart_uploads(
                Bucket=self._bucket, Prefix=key
            )
            for upload in listed.get("Uploads", []):
                if upload["Key"] == key:
                    await client.abort_multipart_upload(
                        Bucket=self._bucket, Key=key, UploadId=upload["UploadId"]
                    )

    async def stat(self, key: str) -> tuple[int, str] | None:
        """The object's size and ETag, or None for an object that is not there.
        An object written in one PUT has its bytes' MD5 as ETag; one joined from
        parts, the MD5 of its parts' MD5s and ``-`` the number of parts
        (``multipart_etag``)."""
        async with self._get_client() as client:
            try:
                head = await client.head_object(Bucket=self._bucket, Key=key)
            except Exception as exc:
                if _object_is_absent(exc):
                    return None
                raise
        return int(head["ContentLength"]), str(head["ETag"]).strip('"')

    def get_url(self, key: str) -> str:
        if self._public_url:
            return f"{self._public_url.rstrip('/')}/{key}"
        if self._endpoint_url:
            return f"{self._endpoint_url}/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"


def multipart_etag(part_md5s: list[str]) -> str:
    """The ETag S3 and R2 give an object joined from parts with these MD5s."""
    joined = b"".join(bytes.fromhex(md5) for md5 in part_md5s)
    return f"{hashlib.md5(joined).hexdigest()}-{len(part_md5s)}"  # noqa: S324


def generate_storage_key(filename: str, prefix: str = "uploads") -> str:
    now = datetime.now(UTC)
    date_path = now.strftime("%Y/%m/%d")
    unique_id = uuid.uuid4().hex[:12]
    ext = Path(filename).suffix.lower() if filename else ""
    return f"{prefix}/{date_path}/{unique_id}{ext}"


def compute_file_hash(file: BinaryIO) -> str:
    hasher = hashlib.md5()
    for chunk in iter(lambda: file.read(8192), b""):
        hasher.update(chunk)
    file.seek(0)
    return hasher.hexdigest()


def private_storage() -> S3StorageBackend:
    """The private bucket, for what holds working files: task bundles and
    archived sandbox homes. With no private bucket configured this refuses
    rather than use the public one."""
    if (
        not settings.s3_endpoint_url
        or not settings.s3_access_key
        or not settings.s3_secret_key
        or not settings.transcript_s3_bucket
    ):
        raise RuntimeError("Private object storage is not configured")
    return S3StorageBackend(
        bucket=settings.transcript_s3_bucket,
        endpoint_url=settings.s3_endpoint_url,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
    )


_storage_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    global _storage_backend
    if _storage_backend is None:
        storage_type = getattr(settings, "storage_type", "local")
        if storage_type == "s3":
            _storage_backend = S3StorageBackend(
                bucket=getattr(settings, "s3_bucket", "cheese"),
                endpoint_url=getattr(settings, "s3_endpoint_url", None),
                access_key=getattr(settings, "s3_access_key", None),
                secret_key=getattr(settings, "s3_secret_key", None),
                region=getattr(settings, "s3_region", "us-east-1"),
                public_url=getattr(settings, "s3_public_url", None),
            )
        else:
            base_path = getattr(settings, "storage_local_path", "./uploads")
            base_url = getattr(settings, "storage_local_url", "/uploads")
            _storage_backend = LocalStorageBackend(base_path, base_url)
    return _storage_backend
