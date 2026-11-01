"""A bucket nobody can reach is an outage to wait out, not a fault of this server.

When the box's way out to the object store is down, every request that needs
the bucket fails until it is back. The caller should hear "unavailable, try
again" (503, retryable) — the answer the API gives for every other dependency
that is not there — rather than an internal server error.
"""

import io
import socket

import pytest

from app.core.errors import BaseError
from app.core.storage import S3StorageBackend

pytestmark = pytest.mark.anyio


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _unreachable_bucket() -> S3StorageBackend:
    return S3StorageBackend(
        bucket="backups",
        endpoint_url=f"http://127.0.0.1:{_closed_port()}",
        access_key="example-access-key",
        secret_key="example-secret-key",
    )


async def test_an_upload_to_an_unreachable_bucket_is_unavailable_not_a_crash():
    with pytest.raises(BaseError) as raised:
        await _unreachable_bucket().upload(
            io.BytesIO(b"payload"), "session-records/k", "application/x"
        )
    assert raised.value.status_code == 503
    assert raised.value.retryable is True


async def test_a_download_from_an_unreachable_bucket_is_unavailable_not_a_crash():
    with pytest.raises(BaseError) as raised:
        await _unreachable_bucket().download("session-records/k")
    assert raised.value.status_code == 503
    assert raised.value.retryable is True


async def test_why_the_bucket_was_unreachable_is_logged(caplog):
    """The 503 says only "unavailable"; a wrong endpoint or a TLS failure must
    still be told apart from an outage by whoever reads the log."""
    with caplog.at_level("WARNING", logger="app.core.storage"):
        with pytest.raises(BaseError) as raised:
            await _unreachable_bucket().upload(
                io.BytesIO(b"payload"), "session-records/k", "application/x"
            )
    cause = type(raised.value.__cause__).__name__
    said = [r.getMessage() for r in caplog.records if r.name == "app.core.storage"]
    assert any(cause in line for line in said), (cause, said)
