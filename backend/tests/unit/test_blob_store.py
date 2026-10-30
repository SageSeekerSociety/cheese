"""Both places a project's file bytes can live keep the same promises.

A file's record says which store holds its bytes, and every reader goes through
the same interface whichever it is. So the two must agree on everything a
reader relies on: what was put comes back unchanged, a missing key is missing
rather than empty, a stretch of a recording is that stretch, and an upload a
browser made in parts is, once finished, exactly the bytes it sent — and
nothing at all until then.
"""

import hashlib
import io

import httpx
import pytest

from app.core import blob_store, storage
from app.core.blob_store import (
    BlobMissingError,
    BlobStore,
    LocalStore,
    PartAddress,
    PartReceipt,
)
from app.core.errors import UnprocessableEntityError
from tests.support.files_bucket import bucket_store, files_bucket

pytestmark = pytest.mark.anyio

#: The smallest part S3 joins when another part follows it.
MIN_PART = 5 * 1024 * 1024


@pytest.fixture(scope="module")
def bucket_config():
    with files_bucket() as config:
        yield config


@pytest.fixture(params=["local", "bucket"])
def store(request, tmp_path) -> BlobStore:
    if request.param == "local":
        return LocalStore(tmp_path)
    return bucket_store(request.getfixturevalue("bucket_config"))


async def _all(store: BlobStore, key: str, start: int, end: int) -> bytes:
    return b"".join([piece async for piece in store.chunks(key, start, end)])


def _receipt(data: bytes) -> PartReceipt:
    return PartReceipt(len(data), hashlib.md5(data).hexdigest())


async def _body(data: bytes):
    yield data


async def _put_part(store: BlobStore, address: PartAddress, data: bytes) -> bool:
    """PUT one part where the store said, as a browser would; whether it was
    taken. A local store's address is this backend's own route, which hands
    the body to the store."""
    if address.url.startswith("/files/incoming/"):
        part = blob_store.open_part(address.url.rsplit("/", 1)[-1])
        assert part is not None and isinstance(store, LocalStore)
        try:
            await store.receive_part(part, _body(data))
        except UnprocessableEntityError:
            return False
        return True
    async with httpx.AsyncClient() as client:
        answer = await client.put(address.url, content=data, headers=address.headers)
    return answer.status_code == 200


async def _upload(store: BlobStore, key: str, parts: list[bytes]) -> str:
    upload_id = await store.begin_upload(key)
    for number, data in enumerate(parts, start=1):
        address = await store.part_address(key, upload_id, number, _receipt(data))
        assert await _put_part(store, address, data)
    return upload_id


async def test_what_was_put_comes_back(store: BlobStore):
    await store.put(".library-blobs/p/one", b"first")
    await store.put(".library-blobs/p/two", io.BytesIO(b"second, from a file"))

    assert await store.get(".library-blobs/p/one") == b"first"
    assert await store.get(".library-blobs/p/two") == b"second, from a file"
    assert await store.size(".library-blobs/p/two") == len(b"second, from a file")


async def test_a_missing_key_is_missing_not_empty(store: BlobStore):
    with pytest.raises(BlobMissingError):
        await store.get(".library-blobs/p/never")
    assert await store.size(".library-blobs/p/never") is None


async def test_a_stretch_is_exactly_that_stretch(store: BlobStore):
    data = bytes(range(256)) * 9000  # over one read piece
    await store.put("k/video", data)

    assert await _all(store, "k/video", 0, len(data) - 1) == data
    assert await _all(store, "k/video", 1000, 1999) == data[1000:2000]
    assert await _all(store, "k/video", len(data) - 10, len(data) - 1) == data[-10:]


async def test_deleted_bytes_are_gone_and_deleting_twice_is_fine(store: BlobStore):
    await store.put("k/gone", b"bye")
    await store.delete("k/gone")
    await store.delete("k/gone")

    with pytest.raises(BlobMissingError):
        await store.get("k/gone")


async def test_an_upload_in_parts_is_its_bytes_once_finished(store: BlobStore):
    first, last = b"a" * MIN_PART, b"the rest"
    key = ".library-blobs/p/data"
    upload_id = await _upload(store, key, [first, last])

    assert await store.size(key) is None
    await store.finish_upload(key, upload_id, [_receipt(first), _receipt(last)])

    assert await store.get(key) == first + last


async def test_a_one_part_upload_is_its_bytes(store: BlobStore):
    key = ".library-blobs/p/small"
    upload_id = await _upload(store, key, [b"hello"])

    await store.finish_upload(key, upload_id, [_receipt(b"hello")])

    assert await store.get(key) == b"hello"


async def test_an_upload_missing_a_part_is_not_finished(store: BlobStore):
    first, last = b"a" * MIN_PART, b"never sent"
    key = ".library-blobs/p/half"
    upload_id = await _upload(store, key, [first])

    with pytest.raises(UnprocessableEntityError):
        await store.finish_upload(key, upload_id, [_receipt(first), _receipt(last)])
    assert await store.size(key) is None


async def test_a_part_of_another_size_never_becomes_the_file(store: BlobStore):
    key = ".library-blobs/p/short"
    upload_id = await store.begin_upload(key)
    meant = b"hello"
    address = await store.part_address(key, upload_id, 1, _receipt(meant))

    taken = await _put_part(store, address, b"hell")
    if taken:
        with pytest.raises(UnprocessableEntityError):
            await store.finish_upload(key, upload_id, [_receipt(meant)])
    assert await store.size(key) is None


async def test_a_part_whose_bytes_are_not_its_digest_never_becomes_the_file(
    store: BlobStore,
):
    """Bytes other than the MD5 a part was given are refused: a real bucket
    refuses the PUT (BadDigest); the test bucket takes it, and the parts are
    then not joined, because a part's ETag is the MD5 of what arrived."""
    key = ".library-blobs/p/forged"
    upload_id = await store.begin_upload(key)
    address = await store.part_address(key, upload_id, 1, _receipt(b"hello"))

    await _put_part(store, address, b"jello")

    with pytest.raises(UnprocessableEntityError):
        await store.finish_upload(key, upload_id, [_receipt(b"hello")])
    assert await store.size(key) is None


async def test_a_finished_or_dropped_upload_cannot_be_finished_again(store: BlobStore):
    key = ".library-blobs/p/once"
    done = await _upload(store, key, [b"once"])
    await store.finish_upload(key, done, [_receipt(b"once")])
    dropped = await _upload(store, ".library-blobs/p/dropped", [b"never"])
    await store.abort_upload(".library-blobs/p/dropped", dropped)

    with pytest.raises(BlobMissingError):
        await store.finish_upload(key, done, [_receipt(b"once")])
    with pytest.raises(BlobMissingError):
        await store.finish_upload(
            ".library-blobs/p/dropped", dropped, [_receipt(b"never")]
        )
    assert await store.size(".library-blobs/p/dropped") is None
    assert await store.get(key) == b"once"


async def test_a_copy_is_the_same_bytes_and_the_original_stays(store: BlobStore):
    await store.put("k/original", b"version one")

    await store.copy("k/original", "k/restored")

    assert await store.get("k/restored") == b"version one"
    assert await store.get("k/original") == b"version one"


async def test_a_copy_of_nothing_says_so(store: BlobStore):
    with pytest.raises(BlobMissingError):
        await store.copy("k/never", "k/restored")


async def test_an_object_too_big_for_one_copy_is_copied_whole(
    bucket_config, monkeypatch
):
    """One CopyObject stops at 5 GiB; a larger object is copied in parts. The
    limit is lowered so a test-sized object takes that path."""
    monkeypatch.setattr(storage, "COPY_OBJECT_MAX_BYTES", MIN_PART)
    monkeypatch.setattr(storage, "COPY_PART_BYTES", MIN_PART)
    store = bucket_store(bucket_config)
    data = bytes(range(256)) * (MIN_PART // 256) * 2 + b"tail"
    await store.put("k/dataset", data)

    await store.copy("k/dataset", "k/dataset-copy")

    assert await store.get("k/dataset-copy") == data


def test_a_large_file_is_cut_into_parts_the_bucket_will_join():
    """A bucket joins at most 10,000 parts, each but the last at least 5 MiB:
    every size is cut so that holds, and the parts add up to the file."""
    for size in (1, blob_store.PART_BYTES, 20 * 1024**3, 1024**4):
        step = blob_store.part_bytes_for(size)
        sizes = blob_store.part_sizes(size, step)
        assert sum(sizes) == size
        assert len(sizes) <= blob_store.MAX_PARTS
        assert all(part >= MIN_PART for part in sizes[:-1])
