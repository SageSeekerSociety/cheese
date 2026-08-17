"""What URL an outside caller must actually send.

A route's path as FastAPI declares it is not reachable: the public origin belongs
to the frontend image's nginx, which forwards the API under `/api` and strips that
one segment on the way through. So the schema is only usable if it publishes that
mount point — otherwise a caller who follows it lands on a 404, or worse, on a
different route that answers 200 (the 1.0 and 2.0 halves of the fused API both own
`topics`, `projects` and `tasks`).

A note on what CAN be tested here, learned the hard way. The schema is generated
from the routes, so schema and router can never disagree: any check that composes
`servers[0].url + path`, strips the gateway prefix back off, and probes the result
is a tautology — it passes for every possible routing table, including the
flattened one that caused the original outage. The properties below are real
because each one brings information from OUTSIDE that loop: the pinned
cross-generation collision set, the pinned 2.0 module list, nginx's config, and
hand-written example URLs. Three layers:

1. HTTP probes — the schema arrives over HTTP and every assertion is a response
   the app really produced.
2. The flattening invariant — removing one `/api` layer from a 2.0 path must land
   on nothing, or on a KNOWN member of the pinned collision set; the pin is what
   makes a silently moved prefix visible.
3. Registration guards — the routers as the app mounts them (public `APIRouter`
   objects, not source text): 2.0 modules stay under `/api`, and no two routes
   claim the same path + method (FastAPI would silently give both to whichever
   registered first).
"""

import importlib
import pkgutil
import re
from collections import defaultdict
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

import app.api.routes as _routes_pkg
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
    ("/topics", "2.0 — bare since #370 step 2, like everything else"),
    ("/connector/my/devices", "connector plane"),
    ("/healthz", "ops"),
]


_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


@pytest.fixture(scope="module")
def raw_client() -> TestClient:
    """A client that speaks to the app the way the gateway does — post-strip.

    Deliberately not the DB-backed `client` fixture: every probe below is answered
    by the router before any handler or dependency runs, so this needs no database
    and stays fast enough to sweep the whole surface.
    """
    return TestClient(app)


@pytest.fixture(scope="module")
def lenient_client() -> TestClient:
    """`raw_client`, but a handler exception becomes a 500 instead of failing
    the test. The cross-wire sweep sends real verbs, so a public endpoint's
    handler can actually run and die on the missing database — for that sweep a
    500 is a *positive* signal (the router matched a live route), not an error.
    """
    return TestClient(app, raise_server_exceptions=False)


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


def test_one_namespace_one_shape(raw_client: TestClient) -> None:
    """Every route is addressed the same way: the mount, then the path.

    Until #370 step 2 this test asserted the opposite — that the two generations
    got DIFFERENT external shapes, `/api/users/...` against `/api/api/topics`,
    because 2.0 routers carried their own `/api` to keep `topics`, `projects`
    and `tasks` from meaning two things at one URL. Those words are owned once
    each now, so the namespace that separated them is gone and there is one
    rule left.

    Note what the old assertion would do today: `_external_url` just joins
    strings, so it would still return `/api/api/topics` for a path that no
    longer exists and pass while testing nothing. Hence the probe below rather
    than string arithmetic.
    """
    schema = _published_schema(raw_client)

    assert _external_url(schema, "/users/auth/login") == "/api/users/auth/login"
    assert _external_url(schema, "/topics") == "/api/topics"
    assert not [p for p in schema["paths"] if p.startswith("/api")], (
        "a route re-acquired the gateway prefix; it would have to be sent as "
        "/api/api/... again"
    )


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


# Retired with #370 step 2: there is no second `/api` layer left to lose, so
# "what happens when a caller drops one" has no subject. What it protected — a
# request answered convincingly by the wrong generation — is now protected
# earlier and more strictly by `test_no_two_routes_want_the_same_url_from_a_caller`
# (two routes may never share an external URL) and by
# `test_no_router_carries_the_gateway_prefix_any_more` (nothing may re-introduce
# the layer). The pinned collision set went with it, having reached zero.


def _module_routers() -> dict[str, list[tuple[str, frozenset[str]]]]:
    """(path, methods) per route module, from the routers the app mounts.

    The same walk ``app.main._discover_routers`` performs: every module-level
    ``APIRouter`` in ``app.api.routes``, deduplicated by object identity so a
    router imported into a second module is counted once. This reads the live
    registration objects, not source text.
    """
    seen: set[int] = set()
    per_module: dict[str, list[tuple[str, frozenset[str]]]] = defaultdict(list)
    for module_info in pkgutil.iter_modules(_routes_pkg.__path__):
        module = importlib.import_module(f"{_routes_pkg.__name__}.{module_info.name}")
        for value in vars(module).values():
            if isinstance(value, APIRouter) and id(value) not in seen:
                seen.add(id(value))
                for route in value.routes:
                    methods = frozenset(getattr(route, "methods", None) or []) - {
                        "HEAD"
                    }
                    path = getattr(route, "path", None)
                    if methods and path:
                        per_module[module_info.name].append((path, methods))
    return per_module


# Every module whose routes live under the 2.0 prefix. A module listed here that
# stops declaring /api paths means its prefix was flattened; an unlisted module
# that starts declaring them is a new 2.0 module and belongs in this list.
def test_no_router_carries_the_gateway_prefix_any_more() -> None:
    """No route may declare `/api` itself (#370 step 2), and this is the inverse
    of the guard it replaces.

    That guard pinned a list of 2.0 modules and failed when one of them lost its
    `/api` — because losing it was how a router silently handed its traffic to
    1.0. With the words no longer shared, the prefix has no job left, and the
    danger runs the other way: a new route copied from an old example, or a
    revert, re-introduces it. Such a route is not broken in any way a caller can
    see — it simply has to be sent as `/api/api/...`, and every client that
    composes the base with the path gets a 404 with no clue why.

    Read off the routers the app actually mounts, not the source text.
    """
    offenders = {
        module: sorted(p for p, _ in routes if p == "/api" or p.startswith("/api/"))
        for module, routes in _module_routers().items()
    }
    offenders = {m: paths for m, paths in offenders.items() if paths}

    assert not offenders, (
        "these routes declare the gateway prefix themselves, so a caller has to "
        f"send it twice: {offenders}"
    )


def test_no_two_routes_claim_the_same_path_and_method() -> None:
    """A duplicate registration is a silent amputation, not an error.

    FastAPI routes first-registered-wins: give two routers the same path and
    method and the second's endpoint simply stops existing, with no warning at
    startup and a green schema. This is the mechanism by which a flattened 2.0
    prefix hands its traffic to 1.0, so duplicates are banned outright.
    """
    claims: dict[tuple[str, str], list[str]] = defaultdict(list)
    for module, routes in _module_routers().items():
        for path, methods in routes:
            for method in methods:
                claims[(re.sub(r"\{[^}]+\}", "{}", path), method)].append(module)

    dupes = {key: mods for key, mods in claims.items() if len(mods) > 1}
    assert not dupes, (
        "routes shadowing each other (first registered wins, the rest go "
        f"silently dead): {dict(sorted(dupes.items()))}"
    )


def test_no_two_routes_want_the_same_url_from_a_caller() -> None:
    """Two routes may never be reachable at the SAME external URL (#370 step 3).

    The test above bans duplicate BACKEND paths. This one asks the question a
    caller asks: given the published mount, what do I type? While the two
    generations shared resource words, a differing answer was the only thing
    keeping them apart — a 1.0 `/x` was typed `/api/x`, a 2.0 `/api/x` was typed
    `/api/api/x`.

    Step 2 removed that difference: external URL is now backend path plus the
    mount, one shape for everything, so two routes wanting the same URL is no
    longer softened by a prefix — it is the silent amputation this whole file
    exists to catch. The assertion held before the flattening and holds after,
    which is what made the flattening checkable rather than hopeful; what it
    guards now is every route added from here on.
    """
    claims: dict[tuple[str, str], list[str]] = defaultdict(list)
    for module, routes in _module_routers().items():
        for path, methods in routes:
            external = _GATEWAY_PREFIX + re.sub(r"\{[^}]+\}", "{}", path)
            for method in methods:
                claims[(external, method)].append(module)

    dupes = {key: mods for key, mods in claims.items() if len(mods) > 1}
    assert not dupes, (
        "two routes answer the same URL a caller would send; one of them is "
        f"unreachable and nothing said so: {dict(sorted(dupes.items()))}"
    )


def test_a_trailing_slash_is_never_a_redirect(lenient_client: TestClient) -> None:
    """One stray `/` must be a 404, not a redirect — behind this gateway a
    slash-redirect cannot be made correct.

    Starlette answers an unmatched `/x/` with a 307 to an ORIGIN-ABSOLUTE
    `Location`, and the client sends that back through nginx, which strips
    another segment. The backend cannot repair it, because it cannot know how
    many prefixes the proxy ahead of it will strip — so `redirect_slashes=False`
    is the fix rather than a workaround, and this pins it.

    It used to be worded as "never reaches the OTHER generation", because the
    second pass landed on 1.0's 赛题 and answered with a success code. There is
    one generation now, so the redirect no longer cross-wires — but it is still
    wrong, and still silent: the caller ends up one segment short of the route
    it asked for.
    """
    schema = _published_schema(lenient_client)
    paths = [p for p in schema["paths"] if p != "/"]
    assert paths, "the schema published nothing to probe"

    redirected = [
        (path, response.headers.get("location", ""))
        for path in paths
        if (
            response := lenient_client.request(
                "GET", _concrete(path) + "/", follow_redirects=False
            )
        ).is_redirect
    ]

    assert not redirected, (
        "these answer a trailing slash with a redirect, whose Location has "
        f"already spent a gateway strip the caller paid for: {redirected}"
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
