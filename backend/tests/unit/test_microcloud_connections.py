"""Connection reuse must preserve tenant isolation and per-call timeouts."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from app.domain.machine import microcloud

pytestmark = pytest.mark.anyio


async def test_reuses_tcp_connection_until_scope_closes():
    peers = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):
            peers.append(self.client_address)
            body = b'{"items": []}'
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    client = microcloud.MicroCloudClient(
        base_url=f"http://127.0.0.1:{server.server_port}", secret="fixture"
    )
    try:
        async with microcloud.reuse_connections():
            assert await client.list_offerings() == []
            assert await client.list_offerings() == []
        async with microcloud.reuse_connections():
            assert await client.list_offerings() == []
        assert peers[0] == peers[1]
        assert peers[2] != peers[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_credentials_cookies_timeouts_and_exception_cleanup(monkeypatch):
    requests = []
    clients = []
    real_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200, json={"items": []}, headers={"Set-Cookie": "session=fixture"}
        )

    def make_client(**kwargs):
        client = real_client(transport=httpx.MockTransport(respond), **kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(microcloud.httpx, "AsyncClient", make_client)
    with pytest.raises(RuntimeError, match="fixture shutdown"):
        async with microcloud.reuse_connections():
            for secret, timeout in [("first", 1), ("first", 7), ("second", 3)]:
                await microcloud.MicroCloudClient(
                    base_url="https://fixture.invalid", secret=secret, timeout=timeout
                ).list_offerings()
            raise RuntimeError("fixture shutdown")
    assert [r.headers["Authorization"] for r in requests] == [
        "Bearer first",
        "Bearer first",
        "Bearer second",
    ]
    assert [r.headers.get("cookie") for r in requests] == [
        None,
        "session=fixture",
        None,
    ]
    assert [r.extensions["timeout"]["read"] for r in requests] == [1, 7, 3]
    assert all(client.is_closed for client in clients)
    await microcloud.MicroCloudClient(
        base_url="https://fixture.invalid", secret="first"
    ).list_offerings()
    assert requests[-1].headers.get("cookie") is None
    assert all(client.is_closed for client in clients)


@pytest.mark.parametrize("failure", [403, "timeout"])
async def test_reuse_preserves_error_conversion(monkeypatch, failure):
    real_client = httpx.AsyncClient

    def respond(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture timeout", request=request)
        return httpx.Response(failure, text="fixture forbidden")

    monkeypatch.setattr(
        microcloud.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    async with microcloud.reuse_connections():
        with pytest.raises(microcloud.MicroCloudError) as error:
            await microcloud.MicroCloudClient(
                base_url="https://fixture.invalid", secret="fixture"
            ).list_offerings()
    assert error.value.status == (403 if failure == 403 else None)
