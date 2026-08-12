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
    ("/api/topics", "2.0 — router carries its own /api"),
    ("/connector/my/devices", "connector plane"),
    ("/healthz", "ops"),
]


_HTTP_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}

# Every (2.0 path, method) whose bare form — one /api layer removed — lands on a
# live route of the OTHER generation. Measured on the live router (this is the
# set the sweep below recomputes on every run) and mirrored as the table in
# docs/api-conventions.md. These are the endpoints where losing a prefix does
# not 404 — it answers, convincingly, from the wrong generation.
_CROSS_WIRED_TODAY = {
    ("/api/projects", "GET"),
    ("/api/projects", "POST"),
    ("/api/projects/{project_id}", "GET"),
    ("/api/projects/{project_id}/members", "GET"),
    ("/api/projects/{project_id}/members", "POST"),
    ("/api/projects/{project_id}/members/{user_handle}", "DELETE"),
    ("/api/tasks/{task_id}", "GET"),
    ("/api/topics", "GET"),
    ("/api/topics", "POST"),
    ("/api/topics/{topic_id}", "GET"),
}


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


def test_flattening_one_api_layer_cross_wires_exactly_the_known_set(
    lenient_client: TestClient,
) -> None:
    """The invariant the double prefix exists to hold, probed on the live router.

    A caller (or a refactor) that loses one `/api` layer sends a 2.0 request to
    the bare path — which the 1.0 generation may also own. For every 2.0 path
    and method the schema declares, this probes the bare form and requires the
    outcome to be nothing (404), a method mismatch (405), or a member of
    ``_CROSS_WIRED_TODAY`` — the measured, frozen set of endpoints where the two
    generations collide.

    Equality is asserted in both directions, so this fails when:
    - a pinned entry stops colliding: its 2.0 path left the `/api` namespace —
      the flattening that caused the recorded outage (a 2.0 router prefix was
      "tidied" to the bare name, handing its traffic to 1.0, first-registered
      wins, no error anywhere);
    - a new collision appears: a new route made a bare name and an `/api` name
      overlap, which widens the blast radius of ever losing a prefix — extend
      the pin knowingly or rename the route.

    Verified against the real defect: flattening ``projects.py``'s prefix to
    ``/projects`` turns this test red. The tautological sweep this replaces
    stayed green through that mutation.
    """
    schema = _published_schema(lenient_client)

    two_oh = sorted(p for p in schema["paths"] if p == "/api" or p.startswith("/api/"))
    assert two_oh, "the 2.0 generation vanished from the schema entirely"

    observed: set[tuple[str, str]] = set()
    for path in two_oh:
        bare = _concrete(path[len(_GATEWAY_PREFIX) :] or "/")
        for verb in schema["paths"][path]:
            if verb.upper() not in _HTTP_METHODS:
                continue
            # 404 = nothing there; 405 = path exists, method doesn't. Anything
            # else (401, 422, 200, 500…) means the router matched the bare path
            # to a live handler — the silent cross-wire this test pins.
            status = lenient_client.request(verb.upper(), bare).status_code
            if status not in (404, 405):
                observed.add((path, verb.upper()))

    missing = _CROSS_WIRED_TODAY - observed
    new = observed - _CROSS_WIRED_TODAY
    assert not missing, (
        f"pinned collisions no longer observed: {sorted(missing)} — their 2.0 "
        "paths left the /api namespace. If a router prefix was flattened, that "
        "is the outage docs/api-conventions.md records; put the prefix back."
    )
    assert not new, (
        f"new cross-generation collisions: {sorted(new)} — a bare route and an "
        "/api route now overlap once one /api layer is lost. Rename one, or "
        "extend _CROSS_WIRED_TODAY and the table in docs/api-conventions.md."
    )


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
_TWO_OH_MODULES = {
    "accept",
    "activities",
    "app_preview",
    "backend_log",
    "blocks",
    "conclusions",
    "cx_spaces",
    "cx_tasks",
    "dashboard",
    "frontend_log",
    "git_http",
    "github_account_link",
    "github_install",
    "machines",
    "market",
    "members",
    "memory",
    "milestones",
    "notifications",
    "ops",
    "projects",
    "roles",
    "scheduler",
    "terminal",
    "topic_members",
    "topics",
    "workspace",
}

# Deliberate bare paths inside 2.0 modules. GitHub calls the App callback at the
# URL registered on github.com, which nginx forwards un-stripped — the route has
# to live at the exact external path.
_TWO_OH_BARE_EXCEPTIONS = {
    "github_install": {"/github/app/callback"},
}


def test_every_2_0_router_keeps_its_routes_under_the_api_prefix() -> None:
    """The guard against the next tidy-up: a 2.0 prefix must never go bare.

    The recorded outage was exactly this edit — `prefix="/api/projects"` cleaned
    to `"/projects"` — and nothing failed until production answered from the
    wrong generation. Membership is checked in both directions so a new 2.0
    module cannot ship unlisted and a flattened one cannot hide.
    """
    strayed: list[str] = []
    unlisted: list[str] = []
    for module, routes in sorted(_module_routers().items()):
        allowed_bare = _TWO_OH_BARE_EXCEPTIONS.get(module, set())
        api_paths = {p for p, _ in routes if p == "/api" or p.startswith("/api/")}
        bare_paths = {p for p, _ in routes if p not in api_paths}
        if module in _TWO_OH_MODULES:
            if not api_paths:
                strayed.append(f"{module}: no /api routes left at all")
            for path in sorted(bare_paths - allowed_bare):
                strayed.append(f"{module}: {path}")
        elif api_paths:
            unlisted.append(f"{module}: {sorted(api_paths)[:3]}")

    assert not strayed, (
        "2.0 routes outside the /api prefix — flattening is the recorded "
        f"outage, not a cleanup: {strayed}"
    )
    assert not unlisted, (
        "modules declaring /api routes but not listed in _TWO_OH_MODULES — "
        f"add them so the prefix stays guarded: {unlisted}"
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
