"""Storage operations reuse HTTP connections without mixing credentials."""

import asyncio
import io
from collections import Counter
from contextlib import asynccontextmanager

import pytest
from aiohttp import web
from botocore.exceptions import ClientError

from app.core.config import settings
from app.core.storage import S3StorageBackend, reuse_s3_connections

pytestmark = pytest.mark.anyio


@asynccontextmanager
async def object_store():
    objects = {}
    peers = set()

    async def handle(request):
        peers.add(request.transport.get_extra_info("peername"))
        if request.method == "PUT":
            objects[request.path] = await request.read()
            return web.Response(headers={"ETag": '"test"'})
        if request.method == "GET":
            return web.Response(body=objects[request.path])
        if request.method == "HEAD":
            if request.path not in objects:
                return web.Response(status=404)
            return web.Response(
                headers={"Content-Length": str(len(objects[request.path]))}
            )
        if request.method == "DELETE":
            objects.pop(request.path, None)
            return web.Response(status=204)
        raise AssertionError(request.method)

    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = runner.addresses[0][1]
    try:
        yield f"http://127.0.0.1:{port}", objects, peers
    finally:
        await runner.cleanup()


def configure(monkeypatch, endpoint):
    monkeypatch.setattr(settings, "storage_type", "local")
    monkeypatch.setattr(settings, "s3_endpoint_url", endpoint)
    monkeypatch.setattr(settings, "s3_access_key", "test-access")
    monkeypatch.setattr(settings, "s3_secret_key", "test-secret")
    monkeypatch.setattr(settings, "transcript_s3_bucket", "private")
    monkeypatch.setattr(settings, "s3_region", "us-east-1")


def backend(endpoint, bucket="private", access_key="test-access"):
    return S3StorageBackend(
        bucket=bucket,
        endpoint_url=endpoint,
        access_key=access_key,
        secret_key="test-secret",
    )


async def test_separate_chunks_reuse_connection_and_preserve_bytes(monkeypatch):
    async with object_store() as (endpoint, objects, peers):
        configure(monkeypatch, endpoint)
        async with reuse_s3_connections():
            for index in range(3):
                await backend(endpoint).upload(
                    io.BytesIO(f"chunk-{index}".encode()), str(index), "text/plain"
                )
            assert await backend(endpoint).download("1") == b"chunk-1"
            assert await backend(endpoint).exists("2")
            assert await backend(endpoint).delete("2")
            assert not await backend(endpoint).exists("2")
            assert objects == {"/private/0": b"chunk-0", "/private/1": b"chunk-1"}
            assert len(peers) == 1
        # A new application lifetime must not keep the old HTTP session alive.
        async with reuse_s3_connections():
            assert await backend(endpoint).exists("0")
        assert len(peers) == 2


async def test_buckets_share_connections_but_credentials_do_not(monkeypatch):
    async with object_store() as (endpoint, objects, peers):
        configure(monkeypatch, endpoint)
        async with reuse_s3_connections():
            await backend(endpoint, "private").upload(
                io.BytesIO(b"a"), "x", "text/plain"
            )
            await backend(endpoint, "public").upload(
                io.BytesIO(b"b"), "x", "text/plain"
            )
            assert len(peers) == 1
            await backend(endpoint, access_key="another-access").upload(
                io.BytesIO(b"c"), "y", "text/plain"
            )
            assert len(peers) == 2
            assert objects["/private/x"] == b"a"
            assert objects["/public/x"] == b"b"


async def test_concurrent_uploads_and_failure_leave_client_usable(monkeypatch):
    async with object_store() as (endpoint, objects, peers):
        configure(monkeypatch, endpoint)
        async with reuse_s3_connections():
            await asyncio.gather(
                *(
                    backend(endpoint, access_key="concurrent-access").upload(
                        io.BytesIO(str(i).encode()), str(i), "text/plain"
                    )
                    for i in range(4)
                )
            )
            assert len(objects) == 4
            client = backend(endpoint, access_key="concurrent-access")
            assert not await client.exists("absent")
            assert await client.download("3") == b"3"
            assert len(peers) <= 4


@pytest.mark.parametrize(
    ("error", "failures", "expected_attempts"),
    [("IncompleteBody", 1, 2), ("IncompleteBody", 99, 3), ("AccessDenied", 99, 1)],
)
async def test_multipart_upload_retries_incomplete_parts_only(
    error, failures, expected_attempts
):
    content = b"task recovery bytes\x00" * 500_000
    attempts = Counter()
    parts = {}
    completed = None
    aborted = False

    async def handle(request):
        nonlocal completed, aborted
        if request.method == "POST" and "uploads" in request.query:
            return web.Response(
                text="<InitiateMultipartUploadResult><UploadId>test-upload</UploadId>"
                "</InitiateMultipartUploadResult>",
                content_type="application/xml",
            )
        if request.method == "PUT":
            number = int(request.query["partNumber"])
            body = await request.read()
            attempts[number] += 1
            if number == 1 and attempts[number] <= failures:
                return web.Response(
                    status=400,
                    text=(
                        f"<Error><Code>{error}</Code>"
                        "<Message>Upload failed</Message></Error>"
                    ),
                    content_type="application/xml",
                )
            parts[number] = body
            return web.Response(headers={"ETag": f'"part-{number}"'})
        if request.method == "POST":
            completed = b"".join(parts[number] for number in sorted(parts))
            return web.Response(
                text='<CompleteMultipartUploadResult><ETag>"complete"</ETag>'
                "</CompleteMultipartUploadResult>",
                content_type="application/xml",
            )
        if request.method == "DELETE":
            aborted = True
            return web.Response(status=204)
        raise AssertionError(request.method)

    app = web.Application(client_max_size=16 * 1024 * 1024)
    app.router.add_route("*", "/{path:.*}", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    endpoint = f"http://127.0.0.1:{runner.addresses[0][1]}"
    try:
        if failures == 1:
            await backend(endpoint).upload(
                io.BytesIO(content), "bundle", "application/x-git-bundle"
            )
            assert completed == content
            assert not aborted
            assert attempts[2] == 1
        else:
            with pytest.raises(ClientError) as failure:
                await backend(endpoint).upload(
                    io.BytesIO(content), "bundle", "application/x-git-bundle"
                )
            assert failure.value.response["Error"]["Code"] == error
            assert completed is None
            assert aborted
        assert attempts[1] == expected_attempts
    finally:
        await runner.cleanup()
