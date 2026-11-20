"""The bytes of the files the platform keeps for a project (#2114).

A file's record names where its bytes are: a *location* and a *key* in it. It
reads them back through the store for that location (:func:`store`). New bytes
go to the store ``settings.files_store`` names (:func:`for_new`); bytes
already stored stay where they were written, so changing the setting moves
nothing.

Two locations: ``local`` is a directory tree under ``workspace_root``;
``bucket`` is the files bucket (``settings.files_bucket``).

An upload from a browser or a machine does not pass through the backend, and
however large it is it arrives the same way: in parts of :func:`part_bytes_for`
bytes, each put straight into the store at an address the store hands out for
that one part (:meth:`BlobStore.part_address`) and carrying its MD5
(``Content-MD5``), which the store checks the bytes against. Once every part is in, the backend joins
them into the object at the key it was given from the start
(:meth:`BlobStore.finish_upload`): the bytes never move after they land, so an
object of any size needs no copy. On the bucket this is S3's multipart upload,
and an upload that is never finished leaves no object: the bucket's lifecycle
drops its parts (R2 does so after seven days by default). On a local store the
parts wait under :data:`INCOMING`.
"""

import asyncio
import base64
import hashlib
import json
import shutil
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

from app.core.config import settings
from app.core.crypto import DecryptionError, Purpose, decrypt, encrypt
from app.core.errors import UnprocessableEntityError
from app.core.sentences import say
from app.core.storage import S3StorageBackend, error_code

LOCAL = "local"
BUCKET = "bucket"

#: Where the parts of an upload to a local store wait until it is finished.
#: A local store's root is shared with the platform's other trees, which are
#: all dot-named.
INCOMING = ".incoming/"

#: How large each part of an upload is: small enough for a browser to hold a
#: few in memory at once and to resend one that failed, large enough that a
#: 20 GB file is a few hundred parts.
PART_BYTES = 64 * 1024 * 1024
#: The most parts S3 and R2 join into one object.
MAX_PARTS = 10_000

#: How long the address for one part stays good: its PUT has to start within it.
PART_URL_SECONDS = 60 * 60
#: How long the token that stands for one upload stays good, from asking for it
#: to registering what arrived: a 20 GB upload over a 1 MB/s line takes under
#: six hours. The bucket drops an unfinished upload's parts days later.
UPLOAD_TOKEN_SECONDS = 24 * 60 * 60

#: How long an address to fetch one stored file stays good: a fetch has to
#: start within it. A machine whose fetch of a large file is cut off asks for a
#: new address and carries on from where it stopped (`cheese library get`).
DOWNLOAD_URL_SECONDS = 10 * 60
#: What a download token is sealed for, so no other sealed value opens as one.
OUTGOING = "/files/outgoing"

#: Bytes to keep: whole in memory, or a readable file that can be rewound.
Content = bytes | BinaryIO

_CHUNK = 1024 * 1024


class BlobMissingError(LookupError):
    """No bytes under this key (or no upload by this id)."""


def part_bytes_for(size: int) -> int:
    """The size of every part but the last of an upload of ``size`` bytes."""
    return max(PART_BYTES, -(-size // MAX_PARTS))


def part_sizes(size: int, part_bytes: int) -> list[int]:
    """The size of each part of an upload of ``size`` bytes, in order."""
    full, rest = divmod(size, part_bytes)
    return [part_bytes] * full + ([rest] if rest else [])


@dataclass(frozen=True)
class PartAddress:
    """Where to PUT one part, and the headers that PUT must carry."""

    url: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PartReceipt:
    """What one part is meant to be: how many bytes, and their MD5 (hex).

    MD5 and not SHA-256: R2 answers a part carrying ``x-amz-checksum-sha256``
    with 501 NotImplemented, and ``Content-MD5`` is the part check it, S3 and
    every S3-compatible store enforce (BadDigest on a mismatch). A part's MD5
    is also its ETag, so the store's own listing says which bytes arrived."""

    size: int
    md5: str


def _content_md5(md5: str) -> str:
    """A hex MD5 the way the ``Content-MD5`` header says it."""
    return base64.b64encode(bytes.fromhex(md5)).decode()


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
    async def copy(self, source: str, key: str) -> None:
        """The bytes under ``source``, also under ``key``, without reading them
        into memory, whatever their size."""

    @abstractmethod
    async def download_url(self, key: str, size: int) -> str:
        """An address that answers a GET with the ``size`` bytes under ``key``
        (a ranged GET too) for whoever starts it within
        :data:`DOWNLOAD_URL_SECONDS`, with no credential of its own. A path
        starting with ``/`` is this backend's own."""

    @abstractmethod
    async def begin_upload(self, key: str) -> str:
        """Start an upload that will end up under ``key``; its id."""

    @abstractmethod
    async def part_address(
        self, key: str, upload_id: str, number: int, part: PartReceipt
    ) -> PartAddress:
        """Where to PUT part ``number`` (from 1) of an upload: exactly
        ``part.size`` bytes, refused unless their MD5 is ``part.md5``.
        A path starting with ``/`` is this backend's own."""

    @abstractmethod
    async def finish_upload(
        self, key: str, upload_id: str, parts: Sequence[PartReceipt]
    ) -> None:
        """Join the parts into the bytes under ``key``. Refused
        (``uploadIncomplete``) unless every part arrived as ``parts`` says, in
        size and MD5; :class:`BlobMissingError` for an upload this store
        does not know, finished or dropped."""

    @abstractmethod
    async def abort_upload(self, key: str, upload_id: str) -> None:
        """Drop an upload and every part it has; one already gone is fine."""


def _incomplete() -> UnprocessableEntityError:
    return UnprocessableEntityError(say("uploadIncomplete"))


class LocalStore(BlobStore):
    """A directory tree; the key is the path under its root."""

    location = LOCAL

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        root = self.root.resolve()
        target = (root / key).resolve()
        if root not in target.parents:
            # i18n-exempt: keys are made by the platform, never typed by a reader
            raise UnprocessableEntityError("blob key escapes the store")
        return target

    def _staging(self, target: Path) -> Path:
        return target.with_name(f".{target.name}.{uuid.uuid4().hex}")

    def _write(self, key: str, content: Content) -> None:
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = self._staging(target)
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

    def _copy(self, source: str, key: str) -> None:
        origin = self._path(source)
        if not origin.is_file():
            raise BlobMissingError(source)
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = self._staging(target)
        try:
            shutil.copyfile(origin, staging)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)

    async def copy(self, source: str, key: str) -> None:
        await asyncio.to_thread(self._copy, source, key)

    async def download_url(self, key: str, size: int) -> str:
        return f"/files/outgoing/{seal_download(SealedFile(LOCAL, key, size))}"

    def _parts(self, upload_id: str) -> Path:
        return self._path(f"{INCOMING}{upload_id}")

    @staticmethod
    def _part_name(number: int, md5: str) -> str:
        # The digest is in the name, so a part is found only as the bytes it
        # was checked to be.
        return f"{number}-{md5}"

    async def begin_upload(self, key: str) -> str:
        upload_id = uuid.uuid4().hex
        await asyncio.to_thread(self._parts(upload_id).mkdir, parents=True)
        return upload_id

    async def part_address(
        self, key: str, upload_id: str, number: int, part: PartReceipt
    ) -> PartAddress:
        token = seal_part(SealedPart(LOCAL, upload_id, number, part.size, part.md5))
        return PartAddress(f"/files/incoming/{token}")

    async def receive_part(
        self, part: "SealedPart", body: AsyncIterator[bytes]
    ) -> None:
        """Write a part that came to this backend's address for it
        (:meth:`part_address`): exactly ``part.size`` bytes whose MD5 is
        ``part.md5``, or nothing."""
        folder = self._parts(part.upload_id)
        if not await asyncio.to_thread(folder.is_dir):
            raise UnprocessableEntityError(say("uploadExpired"))
        target = folder / self._part_name(part.number, part.md5)
        staging = self._staging(target)
        handle = await asyncio.to_thread(staging.open, "wb")
        digest = hashlib.md5()
        written = 0
        try:
            try:
                async for piece in body:
                    written += len(piece)
                    if written > part.size:
                        break
                    digest.update(piece)
                    await asyncio.to_thread(handle.write, piece)
            finally:
                await asyncio.to_thread(handle.close)
            if written != part.size or digest.hexdigest() != part.md5:
                raise _incomplete()
            await asyncio.to_thread(staging.replace, target)
        finally:
            await asyncio.to_thread(staging.unlink, True)

    def _join(self, key: str, upload_id: str, parts: Sequence[PartReceipt]) -> None:
        folder = self._parts(upload_id)
        if not folder.is_dir():
            raise BlobMissingError(upload_id)
        pieces = [
            folder / self._part_name(number, part.md5)
            for number, part in enumerate(parts, start=1)
        ]
        if any(
            not piece.is_file() or piece.stat().st_size != part.size
            for piece, part in zip(pieces, parts, strict=True)
        ):
            raise _incomplete()
        target = self._path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = self._staging(target)
        try:
            with staging.open("wb") as out:
                for piece in pieces:
                    with piece.open("rb") as source:
                        shutil.copyfileobj(source, out, _CHUNK)
            staging.replace(target)
        finally:
            staging.unlink(missing_ok=True)
        shutil.rmtree(folder, ignore_errors=True)

    async def finish_upload(
        self, key: str, upload_id: str, parts: Sequence[PartReceipt]
    ) -> None:
        await asyncio.to_thread(self._join, key, upload_id, parts)

    async def abort_upload(self, key: str, upload_id: str) -> None:
        await asyncio.to_thread(shutil.rmtree, self._parts(upload_id), True)


#: What the bucket answers when the parts it has are not the ones named.
_PARTS_REFUSED = {"InvalidPart", "InvalidPartOrder", "BadDigest", "EntityTooSmall"}


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

    async def copy(self, source: str, key: str) -> None:
        if not await self.bucket.copy(source, key):
            raise BlobMissingError(source)

    async def download_url(self, key: str, size: int) -> str:
        return await self.bucket.presign(key, "get_object", DOWNLOAD_URL_SECONDS)

    async def begin_upload(self, key: str) -> str:
        return await self.bucket.start_parts(key)

    async def part_address(
        self, key: str, upload_id: str, number: int, part: PartReceipt
    ) -> PartAddress:
        # The length and the MD5 are signed in: the bucket refuses a PUT of any
        # other size or without the header (403, the signature no longer
        # matches), and one whose bytes are not that MD5 (400 BadDigest).
        checksum = _content_md5(part.md5)
        url = await self.bucket.presign(
            key,
            "upload_part",
            PART_URL_SECONDS,
            UploadId=upload_id,
            PartNumber=number,
            ContentLength=part.size,
            ContentMD5=checksum,
        )
        return PartAddress(url, {"Content-MD5": checksum})

    async def finish_upload(
        self, key: str, upload_id: str, parts: Sequence[PartReceipt]
    ) -> None:
        found = await self.bucket.parts(key, upload_id)
        if found is None:
            raise BlobMissingError(upload_id)
        arrived = {int(part["PartNumber"]): part for part in found}
        if set(arrived) != set(range(1, len(parts) + 1)) or any(
            int(arrived[number]["Size"]) != part.size
            or str(arrived[number]["ETag"]).strip('"') != part.md5
            for number, part in enumerate(parts, start=1)
        ):
            raise _incomplete()
        # A part's ETag is its MD5: named by the MD5 each part was meant to
        # have, the bucket joins the parts only if those are the bytes it holds
        # (InvalidPart otherwise).
        joined = [
            {"PartNumber": number, "ETag": f'"{part.md5}"'}
            for number, part in enumerate(parts, start=1)
        ]
        try:
            await self.bucket.join_parts(key, upload_id, joined)
        except Exception as exc:
            code = error_code(exc)
            if code == "NoSuchUpload":
                raise BlobMissingError(upload_id) from None
            if code in _PARTS_REFUSED:
                raise _incomplete() from None
            raise

    async def abort_upload(self, key: str, upload_id: str) -> None:
        await self.bucket.abort_parts(key, upload_id)


@dataclass(frozen=True)
class SealedUpload:
    """One upload someone was allowed to make: which store, the key its bytes
    end up under, the store's id for it, how big it is and how it is cut."""

    location: str
    key: str
    upload_id: str
    size: int
    part_bytes: int


@dataclass(frozen=True)
class SealedPart:
    """One part of an upload to a local store, as its address allows it."""

    location: str
    upload_id: str
    number: int
    size: int
    md5: str


@dataclass(frozen=True)
class SealedFile:
    """One stored file someone may fetch."""

    location: str
    key: str
    size: int


def _seal(purpose: Purpose, claims: dict, *, bound_to: str, seconds: int) -> str:
    body = json.dumps({**claims, "until": int(time.time()) + seconds})
    return encrypt(purpose, body, bound_to=bound_to)


def _open(purpose: Purpose, token: str, *, bound_to: str) -> dict | None:
    try:
        claims = json.loads(decrypt(purpose, token, bound_to=bound_to))
    except (DecryptionError, ValueError):
        return None
    if not isinstance(claims, dict) or claims.get("until", 0) < time.time():
        return None
    return claims


def seal_upload(upload: SealedUpload, *, bound_to: str) -> str:
    """A token that stands for ``upload`` for whoever holds it, for
    :data:`UPLOAD_TOKEN_SECONDS`, and only where ``bound_to`` is named again
    when it is opened."""
    return _seal(
        Purpose.FILE_UPLOAD,
        {
            "location": upload.location,
            "key": upload.key,
            "upload_id": upload.upload_id,
            "size": upload.size,
            "part_bytes": upload.part_bytes,
        },
        bound_to=bound_to,
        seconds=UPLOAD_TOKEN_SECONDS,
    )


def open_upload(token: str, *, bound_to: str) -> SealedUpload | None:
    """The upload a token stands for, or None for one that is forged, damaged,
    sealed for somewhere else, or past its time."""
    claims = _open(Purpose.FILE_UPLOAD, token, bound_to=bound_to)
    try:
        return (
            None
            if claims is None
            else SealedUpload(
                location=str(claims["location"]),
                key=str(claims["key"]),
                upload_id=str(claims["upload_id"]),
                size=int(claims["size"]),
                part_bytes=int(claims["part_bytes"]),
            )
        )
    except (KeyError, TypeError, ValueError):
        return None


def seal_part(part: SealedPart) -> str:
    """The token in a local part's address (:meth:`LocalStore.part_address`):
    it is the permission, the way a presigned address's signature is."""
    return _seal(
        Purpose.FILE_UPLOAD,
        {
            "location": part.location,
            "upload_id": part.upload_id,
            "number": part.number,
            "size": part.size,
            "md5": part.md5,
        },
        bound_to=INCOMING,
        seconds=PART_URL_SECONDS,
    )


def open_part(token: str) -> SealedPart | None:
    """The part a local part's address allows, or None for an address that is
    forged, damaged or past its time."""
    claims = _open(Purpose.FILE_UPLOAD, token, bound_to=INCOMING)
    try:
        return (
            None
            if claims is None
            else SealedPart(
                location=str(claims["location"]),
                upload_id=str(claims["upload_id"]),
                number=int(claims["number"]),
                size=int(claims["size"]),
                md5=str(claims["md5"]),
            )
        )
    except (KeyError, TypeError, ValueError):
        return None


def seal_download(stored: SealedFile) -> str:
    """A token that lets whoever holds it read one stored file, for
    :data:`DOWNLOAD_URL_SECONDS`."""
    return _seal(
        Purpose.FILE_DOWNLOAD,
        {"location": stored.location, "key": stored.key, "size": stored.size},
        bound_to=OUTGOING,
        seconds=DOWNLOAD_URL_SECONDS,
    )


def open_download(token: str) -> SealedFile | None:
    """The file a download token stands for, or None for one that is forged,
    damaged or past its time."""
    claims = _open(Purpose.FILE_DOWNLOAD, token, bound_to=OUTGOING)
    try:
        return (
            None
            if claims is None
            else SealedFile(
                location=str(claims["location"]),
                key=str(claims["key"]),
                size=int(claims["size"]),
            )
        )
    except (KeyError, TypeError, ValueError):
        return None


def local() -> LocalStore:
    return LocalStore(Path(settings.workspace_root))


def store(location: str) -> BlobStore:
    """The store for bytes recorded at ``location``."""
    if location == LOCAL:
        return local()
    if location == BUCKET:
        if not settings.files_bucket:
            # i18n-exempt: a deployment missing its bucket, a 500 no reader is told
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
    # i18n-exempt: an invariant broken in the database, a 500 no reader is told
    raise UnprocessableEntityError(f"unknown blob location: {location}")


def for_new() -> BlobStore:
    """Where new bytes are written."""
    return store(settings.files_store)
