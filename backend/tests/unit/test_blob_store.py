"""Both places a project's file bytes can live keep the same promises.

A file's record says which store holds its bytes, and every reader goes through
the same interface whichever it is. So the two must agree on everything a
reader relies on: what was put comes back unchanged, a missing key is missing
rather than empty, a stretch of a recording is that stretch, and an upload a
browser made is the same bytes once it is taken in.
"""

import hashlib
import io

import httpx
import pytest

from app.core import blob_store
from app.core.blob_store import BlobMissingError, BlobStore, LocalStore
from app.core.errors import UnprocessableEntityError
from tests.support.files_bucket import bucket_store, files_bucket

pytestmark = pytest.mark.anyio


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


async def test_the_digest_is_of_the_stored_bytes(store: BlobStore):
    data = b"x" * 3_000_000
    await store.put("k/big", data)

    assert await store.sha256("k/big") == hashlib.sha256(data).hexdigest()


async def test_deleted_bytes_are_gone_and_deleting_twice_is_fine(store: BlobStore):
    await store.put("k/gone", b"bye")
    await store.delete("k/gone")
    await store.delete("k/gone")

    with pytest.raises(BlobMissingError):
        await store.get("k/gone")


async def test_an_upload_taken_in_is_the_bytes_that_were_put(store: BlobStore):
    incoming = blob_store.incoming_key()
    await store.put(incoming, b"uploaded")

    await store.take_in(incoming, ".library-blobs/p/kept")

    assert await store.get(".library-blobs/p/kept") == b"uploaded"
    assert await store.size(incoming) is None


async def test_taking_in_an_upload_that_never_arrived_says_so(store: BlobStore):
    with pytest.raises(BlobMissingError):
        await store.take_in(blob_store.incoming_key(), ".library-blobs/p/kept")


async def test_a_browser_puts_an_upload_straight_into_the_bucket(bucket_config):
    store = bucket_store(bucket_config)
    incoming = blob_store.incoming_key()
    url = await store.upload_url(incoming, 5)

    async with httpx.AsyncClient() as client:
        answer = await client.put(url, content=b"hello")

    assert answer.status_code == 200, answer.text
    assert await store.get(incoming) == b"hello"


async def test_a_local_upload_of_another_size_lands_nothing(tmp_path):
    store = LocalStore(tmp_path)
    incoming = blob_store.incoming_key()

    async def body(data: bytes):
        yield data

    with pytest.raises(UnprocessableEntityError):
        await store.receive(incoming, 5, body(b"hello, and more"))
    with pytest.raises(UnprocessableEntityError):
        await store.receive(incoming, 5, body(b"hi"))
    assert await store.size(incoming) is None

    await store.receive(incoming, 5, body(b"hello"))
    assert await store.get(incoming) == b"hello"
