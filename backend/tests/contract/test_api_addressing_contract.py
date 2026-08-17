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

# EMPTY, as of the 赛题 merge (#370) — and that is the whole point of the issue.
#
# Every entry here was a resource word owned twice, where losing one `/api` layer
# handed a 2.0 request to 1.0 and got a confident answer from the wrong
# generation. They left one at a time: six `/api/projects*` rows when the 知是
# team project moved to `/team-projects`, three `/api/topics*` when the question
# tag moved to `/tags`, and the last one — `/api/tasks/{task_id}` — not by
# renaming but by MERGING: the cheesex 题目 hierarchy is retired and 赛题 are the
# 知是 ones.
#
# So the doubled `/api/api` has no reason left to exist, and #370 step 2 (flatten
# the 2.0 prefix, `BASE` back to `/api`) is unblocked. Until that lands this set
# stays empty and keeps its other direction: a NEW collision fails here, which is
# what stops the surface from re-acquiring one before the flattening happens.
_CROSS_WIRED_TODAY: set[tuple[str, str]] = set()


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
        f"pinned collisions no longer observed: {sorted(missing)}. Two very "
        "different things produce this. (a) A 1.0 route was RENAMED off the "
        "shared word — the collision is genuinely gone, #370's whole point; "
        "shrink this set. (b) A 2.0 router prefix was flattened to the bare "
        "name, which hands its traffic to 1.0 with no error anywhere — that is "
        "the outage docs/api-conventions.md records; put the prefix back. Check "
        "which happened before editing this set."
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
    "agent_credential",
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
    # renamed from `notifications` (#370): 1.0 keeps that word for the social
    # feed, 2.0 reports platform state and now says so.
    "alerts",
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


def test_no_two_routes_want_the_same_url_from_a_caller() -> None:
    """Two routes may never be reachable at the SAME external URL (#370 step 3).

    The test above bans duplicate BACKEND paths. This one asks the question a
    caller asks: given the published mount, what do I type? A 1.0 `/x` is typed
    `/api/x` and a 2.0 `/api/x` is typed `/api/api/x`, so today they differ — and
    that difference is the only thing keeping the two generations apart while
    they still share resource words.

    It matters most for what comes next: #370 step 2 drops the 2.0 prefix, at
    which point external URL and backend path become the same string and any
    surviving shared word becomes a real duplicate. This assertion holds before
    and after that change, so it is what makes the flattening checkable rather
    than hopeful.
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


def test_a_trailing_slash_never_reaches_the_other_generation(
    lenient_client: TestClient,
) -> None:
    """One stray `/` used to convert a correct 2.0 URL into a 1.0 answer.

    The chain, verified live before it was closed: nginx strips one `/api`, the
    backend finds no route for `/api/tasks/7/` and redirects to an
    ORIGIN-ABSOLUTE `/api/tasks/7`, the client follows, nginx strips AGAIN, and
    1.0's 赛题 answers. A success code, from the wrong generation, for a URL that
    was right apart from its last character.

    The backend cannot emit a correct `Location` here — it cannot know how many
    prefixes the proxy ahead of it will strip — so `redirect_slashes=False` is
    the fix rather than a workaround, and this pins it. A 404 is the point: the
    caller learns immediately instead of being handed someone else's data.
    """
    schema = _published_schema(lenient_client)
    two_oh = [p for p in schema["paths"] if p == "/api" or p.startswith("/api/")]
    assert two_oh, "the 2.0 generation vanished from the schema entirely"

    redirected = []
    for path in two_oh:
        response = lenient_client.request(
            "GET", _concrete(path) + "/", follow_redirects=False
        )
        if response.is_redirect:
            redirected.append((path, response.headers.get("location", "")))

    assert not redirected, (
        "these answer a trailing slash with a redirect, which spends an /api the "
        f"caller already paid and lands one generation over: {redirected}"
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
