import asyncio
import hashlib
import io
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

import aiofiles
import aiofiles.os

from app.core.config import settings


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
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url
        self._access_key = access_key
        self._secret_key = secret_key
        self._region = region
        self._public_url = public_url

    @asynccontextmanager
    async def _get_client(self):
        import aioboto3

        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._access_key,
            aws_secret_access_key=self._secret_key,
            region_name=self._region,
        ) as client:
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

    async def download(self, key: str) -> bytes | None:
        async with self._get_client() as client:
            try:
                buffer = io.BytesIO()
                await client.download_fileobj(self._bucket, key, buffer)
                return buffer.getvalue()
            except Exception:
                return None

    async def delete(self, key: str) -> bool:
        async with self._get_client() as client:
            try:
                await client.delete_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False

    async def exists(self, key: str) -> bool:
        async with self._get_client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False

    def get_url(self, key: str) -> str:
        if self._public_url:
            return f"{self._public_url.rstrip('/')}/{key}"
        if self._endpoint_url:
            return f"{self._endpoint_url}/{self._bucket}/{key}"
        return f"https://{self._bucket}.s3.{self._region}.amazonaws.com/{key}"


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
