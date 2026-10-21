"""The bytes of the files the platform keeps for a project (#2114).

A file's record names where its bytes are: a *location* and a *key* in it. It
reads them back through the store for that location (:func:`store`). New bytes
go to the store ``settings.files_store`` names (:func:`for_new`); bytes
already stored stay where they were written, so changing the setting moves
nothing.

Two locations: ``local`` is a directory tree under ``workspace_root``;
``bucket`` is the files bucket (``settings.files_bucket``).

An upload from a browser does not pass through the backend. The browser puts
it under :data:`INCOMING` at an address the store hands out (:meth:`upload_url`),
and once the backend has checked what arrived it is taken in under its own key
(:meth:`take_in`). A put that is never taken in stays under ``.incoming/``; on
the bucket, a lifecycle rule on that prefix expires it.
"""

import asyncio
import hashlib
import json
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings
from app.core.crypto import DecryptionError, Purpose, decrypt, encrypt
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.core.storage import S3StorageBackend

LOCAL = "local"
BUCKET = "bucket"

#: Where an upload lands before the backend has looked at it. Keys are the
#: same in every store, and a local store's root is shared with the
#: platform's other trees, which are all dot-named.
INCOMING = ".incoming/"

#: How long an address to put one upload at stays good.
UPLOAD_URL_SECONDS = 15 * 60

#: Bytes to keep: whole in memory, or a readable file that can be rewound.
Content = bytes | BinaryIO

_CHUNK = 1024 * 1024


class BlobMissingError(LookupError):
    """No bytes under this key."""


def incoming_key() -> str:
    """A fresh key for one upload to land at."""
    return f"{INCOMING}{uuid.uuid4()}"


class BlobStore(ABC):
    location: str

    @abstractmethod
    async def put(self, key: str, content: Content) -> None:
        """Write the bytes under ``key``; a reader never sees half of them."""

    @abstractmethod
    async def get(self, key: str) -> bytes:
        """The bytes under ``key``, or :class:`BlobMissingError`."""

    @abstractmethod
    async def size(self, key: str) -> int | None:
        """How many bytes are under ``key``, or None when there are none."""

    @abstractmethod
    def chunks(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        """Bytes ``start`` to ``end`` (inclusive) under ``key``, a piece at a
        time: a player seeking in a long recording asks for one stretch, and
        two hundred megabytes are not read into memory for it."""

    @abstractmethod
    async def delete(self, key: str) -> None:
        """Remove the bytes under ``key``; none there is not an error."""

    @abstractmethod
    async def upload_url(self, key: str, size: int) -> str:
        """An address a browser PUTs exactly ``size`` bytes to, landing under
        ``key``, with no credential of its own. A path starting with ``/`` is
        this backend's own."""

    @abstractmethod
    async def take_in(self, incoming: str, key: str) -> None:
        """Move what was put under ``incoming`` to ``key``."""

    async def sha256(self, key: str) -> str:
        """The SHA-256 of the bytes under ``key``, read once, a piece at a time."""
        size = await self.size(key)
        if size is None:
            raise BlobMissingError(key)
        digest = hashlib.sha256()
        if size:
            async for piece in self.chunks(key, 0, size - 1):
                digest.update(piece)
        return digest.hexdigest()


class LocalStore(BlobStore):
    """A directory tree; the key is the path under its root."""

    location = LOCAL

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        target = (root / key).resolve()
        if root not in target.parents:
            raise UnprocessableEntityError("blob key escapes the store")
        return target

    def _write(self, key: str, content: Content) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
        try:
            if isinstance(content, bytes):
                staging.write_bytes(content)
            else:
                content.seek(0)
                with staging.open("wb") as out:
                    shutil.copyfileobj(content, out, _CHUNK)
            staging.replace(target)
        finally:
            # A write cut short (a full disk) leaves its staging file behind;
            # after the replace there is nothing here to remove.
            staging.unlink(missing_ok=True)

    async def put(self, key: str, content: Content) -> None:
        await asyncio.to_thread(self._write, key, content)

    def _read(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except (FileNotFoundError, IsADirectoryError):
            raise BlobMissingError(key) from None

    async def get(self, key: str) -> bytes:
        return await asyncio.to_thread(self._read, key)

    def _size(self, key: str) -> int | None:
        target = self._path(key)
        return target.stat().st_size if target.is_file() else None

    async def size(self, key: str) -> int | None:
        return await asyncio.to_thread(self._size, key)

    async def chunks(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        try:
            handle = await asyncio.to_thread(self._path(key).open, "rb")
        except FileNotFoundError:
            raise BlobMissingError(key) from None
        try:
            await asyncio.to_thread(handle.seek, start)
            left = end - start + 1
            while left > 0:
                piece = await asyncio.to_thread(handle.read, min(_CHUNK, left))
                if not piece:
                    return
                left -= len(piece)
                yield piece
        finally:
            await asyncio.to_thread(handle.close)

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._path(key).unlink, True)

    async def upload_url(self, key: str, size: int) -> str:
        token = seal_upload(SealedUpload(LOCAL, key, size), bound_to=INCOMING)
        return f"/files/incoming/{token}"

    def _move(self, incoming: str, key: str) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path(incoming).replace(target)
        except FileNotFoundError:
            raise BlobMissingError(incoming) from None

    async def take_in(self, incoming: str, key: str) -> None:
        await asyncio.to_thread(self._move, incoming, key)

    async def receive(self, key: str, size: int, body: AsyncIterator[bytes]) -> None:
        """Write an upload that came to this backend's address for it
        (:meth:`upload_url`): exactly ``size`` bytes, or nothing."""
        target = self._path(key)
        await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
        staging = target.with_name(f".{target.name}.{uuid.uuid4().hex}")
        handle = await asyncio.to_thread(staging.open, "wb")
        written = 0
        try:
            try:
                async for piece in body:
                    written += len(piece)
                    if written > size:
                        break
                    await asyncio.to_thread(handle.write, piece)
            finally:
                await asyncio.to_thread(handle.close)
            if written != size:
                raise UnprocessableEntityError(say("uploadIncomplete"))
            await asyncio.to_thread(staging.replace, target)
        finally:
            await asyncio.to_thread(staging.unlink, True)


class BucketStore(BlobStore):
    """The files bucket; the key is the object key."""

    location = BUCKET

    def __init__(self, bucket: S3StorageBackend) -> None:
        self.bucket = bucket

    async def put(self, key: str, content: Content) -> None:
        if not isinstance(content, bytes):
            content.seek(0)
        await self.bucket.put(key, content)

    async def get(self, key: str) -> bytes:
        data = await self.bucket.download(key)
        if data is None:
            raise BlobMissingError(key)
        return data

    async def size(self, key: str) -> int | None:
        found = await self.bucket.stat(key)
        return None if found is None else found[0]

    async def chunks(self, key: str, start: int, end: int) -> AsyncIterator[bytes]:
        try:
            async for piece in self.bucket.read_range(key, start, end, _CHUNK):
                yield piece
        except LookupError:
            raise BlobMissingError(key) from None

    async def delete(self, key: str) -> None:
        await self.bucket.delete(key)

    async def upload_url(self, key: str, size: int) -> str:
        # The length is signed in: the bucket refuses a PUT of any other size,
        # so no more than was declared can land.
        return await self.bucket.presign(
            key, "put_object", UPLOAD_URL_SECONDS, ContentLength=size
        )

    async def take_in(self, incoming: str, key: str) -> None:
        if not await self.bucket.move(incoming, key):
            raise BlobMissingError(incoming)


@dataclass(frozen=True)
class SealedUpload:
    """One upload someone was allowed to make: where it lands, how big it is."""

    location: str
    key: str
    size: int


def seal_upload(upload: SealedUpload, *, bound_to: str) -> str:
    """A token that stands for ``upload`` for whoever holds it, for a while,
    and only where ``bound_to`` is named again when it is opened."""
    body = json.dumps(
        {
            "location": upload.location,
            "key": upload.key,
            "size": upload.size,
            "until": int(time.time()) + UPLOAD_URL_SECONDS,
        }
    )
    return encrypt(Purpose.FILE_UPLOAD, body, bound_to=bound_to)


def open_upload(token: str, *, bound_to: str) -> SealedUpload | None:
    """The upload a token stands for, or None for one that is forged, damaged,
    sealed for somewhere else, or past its time."""
    try:
        claims = json.loads(decrypt(Purpose.FILE_UPLOAD, token, bound_to=bound_to))
    except (DecryptionError, ValueError):
        return None
    if claims["until"] < time.time() or not claims["key"].startswith(INCOMING):
        return None
    return SealedUpload(
        location=claims["location"], key=claims["key"], size=int(claims["size"])
    )


def local() -> LocalStore:
    return LocalStore(Path(settings.workspace_root))


def store(location: str) -> BlobStore:
    """The store for bytes recorded at ``location``."""
    if location == LOCAL:
        return local()
    if location == BUCKET:
        if not settings.files_bucket:
            raise RuntimeError(
                "Bytes are recorded in the files bucket, but none is configured"
            )
        return BucketStore(
            S3StorageBackend(
                bucket=settings.files_bucket,
                endpoint_url=settings.files_bucket_endpoint_url,
                access_key=settings.files_bucket_access_key,
                secret_key=settings.files_bucket_secret_key,
                region=settings.files_bucket_region,
            )
        )
    raise UnprocessableEntityError(f"unknown blob location: {location}")


def for_new() -> BlobStore:
    """Where new bytes are written."""
    return store(settings.files_store)
