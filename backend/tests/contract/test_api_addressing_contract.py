"""What URL an outside caller must actually send.

A route's path as FastAPI declares it is not reachable: the public origin belongs
to the frontend image's nginx, which forwards the API under `/api` and strips that
one segment on the way through. So the schema is only usable if it publishes that
mount point — otherwise a caller who follows it lands on a 404, or worse, on a
different route that answers 200 (the 1.0 and 2.0 halves of the fused API both own
`topics`, `projects` and `tasks`).

These tests drive the real app through a stand-in for that gateway, at the URLs the
published schema tells a caller to use. Nothing here reads application source — the
schema arrives over HTTP, and every assertion is a response the app really produced.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

# nginx's `location /api/ { proxy_pass …:8081/; }`, as far as a caller is concerned:
# the one leading segment named below is removed, and the rest reaches the backend
# verbatim. Written out here rather than imported so the test fails if the app and
# the deployment ever disagree about it.
_GATEWAY_PREFIX = "/api"

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NGINX_CONF = _REPO_ROOT / "frontend" / "nginx.conf"

# One live endpoint from each family the backend serves, so the sweep below is not
# the only thing standing between us and a family that stops being addressable.
_FAMILIES = [
    ("/users/auth/login", "1.0 — bare router"),
    ("/api/topics", "2.0 — router carries its own /api"),
    ("/connector/my/devices", "connector plane"),
    ("/healthz", "ops"),
]


@pytest.fixture(scope="module")
def raw_client() -> TestClient:
    """A client that speaks to the app the way the gateway does — post-strip.

    Deliberately not the DB-backed `client` fixture: every probe below is answered
    by the router before any handler or dependency runs, so this needs no database
    and stays fast enough to sweep the whole surface.
    """
    return TestClient(app)


def _through_gateway(external_url: str) -> str:
    """The path the backend receives when a caller sends `external_url`."""
    if external_url == _GATEWAY_PREFIX or external_url.startswith(
        _GATEWAY_PREFIX + "/"
    ):
        return external_url[len(_GATEWAY_PREFIX) :] or "/"
    return external_url


def _published_schema(raw_client: TestClient) -> dict:
    response = raw_client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _external_url(schema: dict, path: str) -> str:
    """The URL the schema tells a caller to send for `path`."""
    return schema["servers"][0]["url"].rstrip("/") + path


def _concrete(path: str) -> str:
    """A template filled in with a value every path converter accepts."""
    return re.sub(r"\{[^}]+\}", "7", path)


def _unused_method(operations: dict) -> str | None:
    """A verb the route does not implement.

    Probing with one gets the router to answer without running a handler: an
    existing path replies 405, a missing one replies 404. That difference is the
    whole assertion, and it costs no side effects.
    """
    declared = {verb.upper() for verb in operations}
    for candidate in ("PATCH", "DELETE", "PUT", "POST", "GET"):
        if candidate not in declared:
            return candidate
    return None


def test_schema_publishes_the_mount_point_callers_have_to_use(
    raw_client: TestClient,
) -> None:
    """The schema names the gateway mount first, so server + path is a real URL."""
    schema = _published_schema(raw_client)

    servers = schema.get("servers")
    assert servers, "schema publishes no server, so its paths address nothing"
    assert servers[0]["url"] == _GATEWAY_PREFIX
    assert [s["url"] for s in servers].count("/") == 1, (
        "direct backend-port access must stay described too"
    )
    for server in servers:
        assert server.get("description"), "a bare URL does not say when to pick it"


def test_the_two_api_generations_get_different_external_shapes(
    raw_client: TestClient,
) -> None:
    """The doubled `/api/api/...` is derivable from the schema, not folklore.

    1.0 routers are bare and 2.0 routers carry their own `/api`; both hang off the
    same published mount, which is exactly why one ends up single-prefixed and the
    other doubled. A caller that composes server + path gets each right without
    having to know the history.
    """
    schema = _published_schema(raw_client)

    assert _external_url(schema, "/users/auth/login") == "/api/users/auth/login"
    assert _external_url(schema, "/api/topics") == "/api/api/topics"


@pytest.mark.parametrize(("path", "family"), _FAMILIES)
def test_every_family_answers_at_its_published_url(
    raw_client: TestClient, path: str, family: str
) -> None:
    """Each half of the fused API is reachable at the URL the schema advertises."""
    schema = _published_schema(raw_client)
    assert path in schema["paths"], f"{family}: {path} is not published at all"

    probe = _unused_method(schema["paths"][path])
    assert probe, f"{family}: {path} implements every verb, nothing left to probe"
    url = _through_gateway(_concrete(_external_url(schema, path)))

    assert raw_client.request(probe, url).status_code != 404, (
        f"{family}: the schema sends callers to "
        f"{_external_url(schema, path)}, which reaches nothing"
    )


def test_no_published_url_is_a_dead_end(raw_client: TestClient) -> None:
    """Sweep the surface: following the schema must never land on nothing.

    This is the check that would have caught the reported defect. With no server
    published, composing the advertised paths against the origin sent 122 of the
    2.0 routes to a 404 and 6 more to an unrelated 1.0 route.
    """
    schema = _published_schema(raw_client)

    dead_ends: list[str] = []
    for path, operations in sorted(schema["paths"].items()):
        probe = _unused_method(operations)
        if probe is None:
            continue
        url = _through_gateway(_concrete(_external_url(schema, path)))
        if raw_client.request(probe, url).status_code == 404:
            dead_ends.append(_external_url(schema, path))

    assert not dead_ends, (
        f"{len(dead_ends)} published URLs reach no route, e.g. {dead_ends[:5]}"
    )


@pytest.mark.skipif(
    not _NGINX_CONF.exists(), reason="frontend image config not in this checkout"
)
def test_the_deployed_gateway_still_strips_what_the_schema_assumes() -> None:
    """Guard the seam the schema depends on.

    The published mount is only correct while nginx keeps stripping exactly one
    `/api`, which is a property of the trailing slash on `proxy_pass`. Drop that
    slash and every 2.0 URL in the schema silently gains a segment, so the two
    layers get pinned together here rather than discovered by a caller.
    """
    conf = _NGINX_CONF.read_text()

    block = re.search(r"location\s+/api/\s*\{(?P<body>.*?)\}", conf, re.DOTALL)
    assert block, "the /api forward disappeared from the frontend image's nginx"

    proxy_pass = re.search(r"proxy_pass\s+(?P<target>\S+);", block.group("body"))
    assert proxy_pass, "the /api location forwards nowhere"
    assert proxy_pass.group("target").endswith("/"), (
        "proxy_pass lost its trailing slash, so nginx no longer strips /api — "
        "the schema's published server is now wrong by one segment"
    )
