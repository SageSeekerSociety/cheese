"""S3 must not report "cannot reach the bucket" as "the object is gone".

`AttachmentService.delete` drops the database row — the only pointer to the
object — once `delete` says False and `exists` agrees the object is not there.
When both of those answers came from a swallowed connection error, the row went
and the object stayed, costing storage and still fetchable by key, while the
caller was told 204.
"""

import io
from contextlib import asynccontextmanager

import pytest

from app.core.storage import S3StorageBackend


class _ClientError(Exception):
    """Shaped like botocore's, which this module reads without importing it."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _FakeS3:
    def __init__(self, failure: Exception) -> None:
        self.failure = failure

    async def head_object(self, **_kwargs):
        raise self.failure

    async def delete_object(self, **_kwargs):
        raise self.failure

    async def download_fileobj(self, *_args, **_kwargs):
        raise self.failure


def _backend(failure: Exception) -> S3StorageBackend:
    backend = S3StorageBackend(bucket="b", endpoint_url="http://s3.invalid")

    @asynccontextmanager
    async def _client():
        yield _FakeS3(failure)

    backend._get_client = _client  # type: ignore[method-assign]
    return backend


ABSENT = [_ClientError("404"), _ClientError("NoSuchKey"), _ClientError("NotFound")]
UNREACHABLE = [
    ConnectionError("connection refused"),
    _ClientError("403"),
    _ClientError("500"),
    TimeoutError("no answer"),
]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ABSENT)
async def test_a_missing_object_is_reported_as_missing(failure: Exception) -> None:
    backend = _backend(failure)
    assert await backend.exists("k") is False
    assert await backend.delete("k") is False
    assert await backend.download("k") is None


@pytest.mark.anyio
@pytest.mark.parametrize("failure", UNREACHABLE)
async def test_a_bucket_that_did_not_answer_is_not_an_absence(
    failure: Exception,
) -> None:
    backend = _backend(failure)
    with pytest.raises(type(failure)):
        await backend.exists("k")
    with pytest.raises(type(failure)):
        await backend.delete("k")
    with pytest.raises(type(failure)):
        await backend.download("k")


@pytest.mark.anyio
async def test_a_readable_object_still_comes_back() -> None:
    backend = S3StorageBackend(bucket="b", endpoint_url="http://s3.invalid")

    class _Ok:
        async def download_fileobj(self, _bucket, _key, buffer: io.BytesIO):
            buffer.write(b"hello")

        async def head_object(self, **_kwargs):
            return {}

        async def delete_object(self, **_kwargs):
            return {}

    @asynccontextmanager
    async def _client():
        yield _Ok()

    backend._get_client = _client  # type: ignore[method-assign]
    assert await backend.download("k") == b"hello"
    assert await backend.exists("k") is True
    assert await backend.delete("k") is True
