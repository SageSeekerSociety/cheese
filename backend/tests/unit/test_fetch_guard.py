"""A fetch reads the public internet and nothing behind the platform's own walls.

The backend fetches from inside the deployment's network. These pin the rules a
person could state without reading the code: a stranger cannot make it fetch at
all; a signed-in caller cannot point it at an internal address, directly or by
way of a redirect; and a refusal is final — the URL is not passed on to the
third-party reader or the browser further down the ladder.

Real sockets on the loopback interface stand in for internal services. One of
them, on 127.0.0.2, plays a public site: the tests declare that one address
public and nothing else, so the checks run as they do in production.
"""

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from fastapi.testclient import TestClient

from app.domain.fetch import guard, service

PAGE = "<html><body><article>" + "一段正文。" * 400 + "</article></body></html>"


class _Server:
    """A loopback HTTP server that counts what reached it."""

    def __init__(self, host: str, respond) -> None:
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self) -> None:
                outer.hits.append(self.path)
                status, headers, body = respond(self)
                data = body.encode()
                self.send_response(status)
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = _answer
            do_POST = _answer

            def log_message(self, *args) -> None:
                pass

        self._httpd = HTTPServer((host, 0), Handler)
        self.url = f"http://{host}:{self._httpd.server_address[1]}"
        threading.Thread(target=self._httpd.serve_forever, daemon=True).start()

    def close(self) -> None:
        self._httpd.shutdown()


@pytest.fixture
def internal() -> Iterator[_Server]:
    """A service only the backend's own network can see."""
    server = _Server(
        "127.0.0.1",
        lambda _r: (200, {"content-type": "text/html"}, "INTERNAL " + PAGE),
    )
    yield server
    server.close()


@pytest.fixture
def public_host(monkeypatch: pytest.MonkeyPatch):
    """Treat 127.0.0.2 as a public address, and only that one."""
    real = guard._is_public
    monkeypatch.setattr(guard, "_is_public", lambda a: a == "127.0.0.2" or real(a))


@pytest.fixture
def downstream() -> Iterator[_Server]:
    """The reader and browser endpoints: they must never hear of a refused URL."""
    server = _Server(
        "127.0.0.1", lambda _r: (200, {"content-type": "application/json"}, "{}")
    )
    yield server
    server.close()


def test_a_stranger_cannot_make_the_server_fetch(internal: _Server) -> None:
    from app.main import app

    r = TestClient(app).post("/fetch", json={"url": internal.url + "/admin"})

    assert r.status_code == 401
    assert internal.hits == []


async def test_an_internal_address_is_refused_before_anything_reads_it(
    internal: _Server, downstream: _Server
) -> None:
    for url in (
        internal.url + "/admin",
        internal.url.replace("127.0.0.1", "localhost") + "/admin",
    ):
        got = await service.fetch(
            url, reader_endpoint=downstream.url, browser_endpoint=downstream.url
        )
        assert not got.ok and "INTERNAL" not in got.text

    assert internal.hits == []
    assert downstream.hits == []


async def test_a_public_page_that_redirects_inward_is_refused(
    public_host, internal: _Server, downstream: _Server
) -> None:
    site = _Server(
        "127.0.0.2", lambda _r: (302, {"location": internal.url + "/admin"}, "")
    )
    try:
        got = await service.fetch(
            site.url + "/article",
            reader_endpoint=downstream.url,
            browser_endpoint=downstream.url,
        )
    finally:
        site.close()

    assert not got.ok and "INTERNAL" not in got.text
    assert site.hits, "the public page was asked"
    assert internal.hits == []
    assert downstream.hits == [], "a refused URL must not go to the reader or browser"


async def test_a_public_page_is_still_read(public_host) -> None:
    site = _Server("127.0.0.2", lambda _r: (200, {"content-type": "text/html"}, PAGE))
    try:
        got = await service.fetch(site.url + "/article")
    finally:
        site.close()

    assert got.ok and "一段正文" in got.text


@pytest.mark.parametrize(
    "rung", ["rung_markdown_native", "rung_plain_http", "rung_impersonated"]
)
async def test_every_rung_that_reads_the_page_itself_refuses_an_inward_redirect(
    public_host, internal: _Server, rung: str
) -> None:
    """Each rung is its own way out of the network, so each one is held to it:
    the ladder stopping at the first refusal must not be what keeps a later
    rung honest."""
    from app.domain.fetch import layers

    site = _Server(
        "127.0.0.2", lambda _r: (302, {"location": internal.url + "/admin"}, "")
    )
    try:
        with pytest.raises(guard.NotPublic):
            await getattr(layers, rung)(site.url + "/article")
    finally:
        site.close()

    assert internal.hits == []
