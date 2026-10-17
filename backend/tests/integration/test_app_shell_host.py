"""The mini-app shell's host on the content domain.

It serves the shell and nothing else — no platform route answers there — and
says, in headers the page cannot lift, that what runs in it has no network,
no storage of its own and may be framed by the platform alone.
"""

import httpx
import pytest

from app.api import app_shell_host
from app.core.config import settings

SHELL = (
    b"<!doctype html><html><head></head><body><div id='cheese-app'></div></body></html>"
)


@pytest.fixture
def host(monkeypatch):
    monkeypatch.setattr(settings, "sites_domain", "sites.localhost")
    monkeypatch.setattr(settings, "sites_scheme", "http")
    monkeypatch.setattr(settings, "sites_port", None)
    monkeypatch.setattr(settings, "frontend_url", "http://platform.localhost")
    monkeypatch.setattr(settings, "miniapp_shell_url", "http://frontend/miniapp")
    asked: list[str] = []

    def frontend(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if request.url.path == "/miniapp/v1/index.html":
            return httpx.Response(200, content=SHELL)
        return httpx.Response(404)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        app_shell_host.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(frontend), **kw),
    )
    return {"Host": "apps.sites.localhost"}


def test_the_shell_is_served_under_rules_that_shut_the_app_in(client, host):
    served = client.get("/v1/", headers=host)
    assert served.status_code == 200
    assert served.content == SHELL
    policy = served.headers["content-security-policy"]
    assert "sandbox allow-scripts;" in policy
    assert "allow-same-origin" not in policy
    assert "connect-src 'none'" in policy
    assert "frame-ancestors http://platform.localhost" in policy


def test_the_shell_travels_compressed_when_the_browser_takes_it(client, host):
    served = client.get("/v1/", headers={**host, "Accept-Encoding": "gzip"})
    assert served.status_code == 200
    # The client unpacks it; what went over the wire was gzip.
    assert served.headers.get("content-encoding") == "gzip"
    assert served.content == SHELL


def test_no_platform_route_answers_on_the_shells_host(client, host):
    for path in ("/users/me", "/apps/q7m2kx4t", "/v9/", "/v1/other.js"):
        answered = client.get(path, headers=host)
        assert answered.status_code == 404, path
