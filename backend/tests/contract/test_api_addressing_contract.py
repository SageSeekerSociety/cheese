"""What URL an outside caller must actually send.

A route's path as FastAPI declares it is not reachable: the public origin belongs
to the frontend image's nginx, which forwards the API under `/api` and strips that
one segment on the way through. So the schema is only usable if it publishes that
mount point — otherwise a caller who follows it lands on a 404, or worse, on a
different route that answers 200 (the 1.0 and 2.0 halves of the fused API both own
`topics`, `projects`, `tasks` and `spaces`).

Two things this file deliberately does NOT do, because the first version did and
they bought nothing:

* It does not sweep every published path through `server + path` and then back
  through the gateway strip. Composing `/api` on and stripping `/api` off is the
  identity, so every probe folded back onto the path FastAPI had declared and the
  sweep asserted only that FastAPI can route its own routes. It stayed green with
  the 2.0 `projects` prefix flattened — the exact outage this contract exists to
  prevent.
* It does not derive "which routes are 2.0" from where those routes currently sit.
  Flattening a prefix removes paths, and a check computed from what is present
  cannot notice an absence. The pins below are therefore written down: the tags
  each generation owns, and the exact set of paths that would cross generations.
  Both go red when a 2.0 prefix is flattened, because a flattened router keeps its
  tag and leaves the collision set.

Everything else here drives the real app over HTTP.
"""

import re
from pathlib import Path
from urllib.parse import urljoin

import pytest
from fastapi.testclient import TestClient
from httpx import AsyncClient

from app.main import app

# nginx's `location /api/ { proxy_pass …:8081/; }`, as far as a caller is concerned:
# the one leading segment named below is removed, and the rest reaches the backend
# verbatim. Written out here rather than imported so the test fails if the app and
# the deployment ever disagree about it.
_GATEWAY_PREFIX = "/api"

# The one regex `location` that wins over `/api/` and forwards the URI untouched:
# an embedded terminal/app page resolves its own assets against `location.pathname`,
# so the backend has to see the exact path the browser asked for.
_UNSTRIPPED_PASSTHROUGH = re.compile(r"^/api/topics/[^/]+/(terminal|app)(/|$)")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_NGINX_CONF = _REPO_ROOT / "frontend" / "nginx.conf"

_HTTP_VERBS = ("get", "put", "post", "delete", "patch", "options", "head", "trace")

# One live endpoint per family, on BOTH sides of every resource name the two
# generations share. A family that stops being addressable fails here by name
# instead of as a number in a sweep.
_FAMILIES = [
    ("/users/auth/login", "1.0 — bare router"),
    ("/projects", "1.0 — contested with 2.0 projects"),
    ("/topics", "1.0 — contested with 2.0 topics"),
    ("/tasks", "1.0 — contested with 2.0 tasks"),
    ("/spaces/{spaceId}", "1.0 — contested with 2.0 spaces"),
    ("/api/projects", "2.0 — router carries its own /api"),
    ("/api/topics", "2.0 — router carries its own /api"),
    ("/api/tasks/{task_id}", "2.0 — router carries its own /api"),
    ("/api/spaces/{space_id}/dashboard", "2.0 — router carries its own /api"),
    ("/connector/my/devices", "connector plane"),
    ("/healthz", "ops"),
]

# The OpenAPI tag each 2.0 router carries for a resource name the 1.0 half also
# owns. A tag travels with its router, so flattening a prefix moves the tag's
# operations out from under the mount instead of hiding them — which is what makes
# this pin able to see an absence. The 1.0 halves carry different tags
# (`TeamProjects`, `Topics`, `Tasks`, `Spaces`), so the split is unambiguous.
_CONTESTED_2_0_TAGS = ("projects", "topics", "tasks", "spaces")

# Every published 2.0 path whose flattened form is a path the 1.0 half also serves,
# with the verbs the two generations would then both claim. Sending the flattened
# URL reaches the 1.0 route: not a 404, a 200 from the wrong domain. Keeping the
# doubled `/api/api/...` shape is what keeps these apart, and the list is spelled
# out so that flattening a prefix (entries leave) and introducing a fresh collision
# (entries arrive) both fail loudly.
_WOULD_CROSS_GENERATIONS = {
    "/api/projects": ("GET", "POST"),
    "/api/projects/{project_id}": ("GET",),
    "/api/projects/{project_id}/members": ("GET", "POST"),
    "/api/projects/{project_id}/members/{user_handle}": ("DELETE",),
    "/api/tasks/{task_id}": ("GET",),
    "/api/topics": ("GET", "POST"),
    "/api/topics/{topic_id}": ("GET",),
}


@pytest.fixture(scope="module")
def raw_client() -> TestClient:
    """A client that speaks to the app the way the gateway does — post-strip.

    Deliberately not the DB-backed `client` fixture: every probe below is answered
    by the router before any handler or dependency runs, so this needs no database
    and stays fast enough to sweep the whole surface.
    """
    return TestClient(app)


def _through_gateway(external_url: str) -> str | None:
    """The path the backend receives, or None when the backend never sees this URL.

    A model of the three `location` blocks in the frontend image's nginx, in the
    order nginx applies them. `None` is the case worth keeping: a URL the gateway
    does not forward is not an error, it is the SPA's `index.html` with a 200 on
    it — so a test that treated "not forwarded" as "the backend answered" would
    pass on exactly the mistake this file is about. Each branch is pinned against
    the real config by the nginx test at the bottom.
    """
    if _UNSTRIPPED_PASSTHROUGH.match(external_url):
        return external_url
    if external_url == _GATEWAY_PREFIX or external_url.startswith(
        _GATEWAY_PREFIX + "/"
    ):
        return external_url[len(_GATEWAY_PREFIX) :] or "/"
    if external_url.startswith("/connector/"):
        return external_url
    return None


def _published_schema(raw_client: TestClient) -> dict:
    response = raw_client.get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _external_url(schema: dict, path: str) -> str:
    """The URL the schema tells a caller to send for `path`."""
    servers = schema.get("servers")
    assert servers, (
        "the schema publishes no server, so it does not say where to send "
        f"{path!r} — that is the defect this file exists to catch"
    )
    return servers[0]["url"].rstrip("/") + path


def _concrete(path: str) -> str:
    """A template filled in with a value every path converter accepts."""
    return re.sub(r"\{[^}]+\}", "7", path)


def _shape(path: str) -> str:
    """A path template with its parameter NAMES erased.

    The router matches on shape, and the two generations spell the same parameter
    differently (`/api/projects/{project_id}` against `/projects/{projectId}`), so
    comparing the templates verbatim would miss every collision that matters.
    """
    return re.sub(r"\{[^}]+\}", "{}", path)


def _verbs(operations: dict) -> set[str]:
    return {verb.upper() for verb in operations if verb in _HTTP_VERBS}


def _unused_method(operations: dict) -> str | None:
    """A verb the route does not implement.

    Probing with one gets the router to answer without running a handler: an
    existing path replies 405, a missing one replies 404. That difference is the
    whole assertion, and it costs no side effects.
    """
    declared = _verbs(operations)
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
    external = _external_url(schema, path)
    url = _through_gateway(_concrete(external))
    assert url is not None, (
        f"{family}: the schema sends callers to {external}, which the gateway does "
        "not forward to the backend at all — that URL serves the SPA"
    )

    assert raw_client.request(probe, url).status_code != 404, (
        f"{family}: the schema sends callers to {external}, which reaches nothing"
    )


@pytest.mark.parametrize("tag", _CONTESTED_2_0_TAGS)
def test_the_2_0_generation_stays_behind_its_own_api_prefix(
    raw_client: TestClient, tag: str
) -> None:
    """A 2.0 router that shares a resource name with 1.0 keeps its `/api` prefix.

    Dropping it is the tidy-looking edit that caused the outage: the route does not
    disappear, it moves on top of the 1.0 route of the same name and one of the two
    stops being reachable. The tag stays put when the prefix moves, so this notices
    the move rather than merely failing to find what is gone.
    """
    schema = _published_schema(raw_client)

    tagged = [
        (path, verb)
        for path, item in schema["paths"].items()
        for verb, operation in item.items()
        if verb in _HTTP_VERBS and tag in operation.get("tags", ())
    ]
    assert tagged, (
        f"no operation is tagged {tag!r} any more — either the 2.0 router was "
        "removed or its tag was renamed, and this guard is now watching nothing"
    )

    flattened = sorted({path for path, _ in tagged if not path.startswith("/api/")})
    assert not flattened, (
        f"{tag!r} is a resource name both API generations own, and these paths "
        f"left the /api mount: {flattened}. They now sit on top of the 1.0 routes "
        "of the same name, so one generation is unreachable."
    )


def test_flattening_the_mount_would_cross_the_two_generations(
    raw_client: TestClient,
) -> None:
    """Measure the collision the doubled prefix is holding apart.

    This is the reason the shape cannot simply be tidied up, so it is measured from
    the live app rather than asserted in a comment: for each published 2.0 path,
    take the flattened form a caller would reach by dropping one `/api` and ask
    whether the 1.0 half serves it too.

    The set is pinned exactly. It shrinks when a 2.0 prefix is flattened (its paths
    stop being 2.0 paths) and grows when a new route lands on a name the other
    generation already owns — both are changes someone has to look at.
    """
    schema = _published_schema(raw_client)
    paths = schema["paths"]

    one_oh: dict[str, set[str]] = {}
    for path, item in paths.items():
        if not path.startswith("/api/"):
            one_oh.setdefault(_shape(path), set()).update(_verbs(item))

    crossing: dict[str, tuple[str, ...]] = {}
    dead = 0
    for path, item in paths.items():
        if not path.startswith("/api/"):
            continue
        verbs = _verbs(item)
        twin = one_oh.get(_shape(path[len(_GATEWAY_PREFIX) :]), set())
        dead += len(verbs - twin)
        if shared := verbs & twin:
            crossing[path] = tuple(sorted(shared))

    assert crossing == _WOULD_CROSS_GENERATIONS, (
        "the set of endpoints that would answer from the WRONG generation if the "
        f"2.0 mount were flattened has changed:\n  now: {sorted(crossing)}\n"
        f"  pinned: {sorted(_WOULD_CROSS_GENERATIONS)}"
    )
    assert dead > 100, (
        f"only {dead} 2.0 operations would 404 without the mount — the published "
        "server no longer carries the whole 2.0 surface"
    )

    # A 404 is the harmless half; these answer, which is why the doubling matters.
    for path, verbs in sorted(crossing.items()):
        flat = _concrete(path[len(_GATEWAY_PREFIX) :])
        for verb in verbs:
            status = raw_client.request(verb, flat).status_code
            assert status != 404, (
                f"{verb} {flat} was expected to reach the 1.0 route that shadows "
                f"{path}, but nothing is there (got {status})"
            )


@pytest.mark.anyio
async def test_a_2_0_resource_is_served_at_its_published_url(
    authed_client: AsyncClient,
) -> None:
    """Create through the published URL, read it back through the published URL.

    The checks above are about placement; this one spends a real request on a real
    row, because the contract being kept is "follow the schema and you reach the
    resource", not "the schema is shaped a certain way". Flatten the 2.0 projects
    prefix and the create below 404s.

    The client speaks to the app post-strip, the way the gateway hands a request
    over, so the URLs read here are the external ones minus the segment nginx eats.
    """
    schema = (await authed_client.get("/openapi.json")).json()
    assert "/api/projects" in schema["paths"], (
        "the 2.0 projects collection is not published, so no caller can find it"
    )
    external = _external_url(schema, "/api/projects")
    assert external == "/api/api/projects"
    backend_path = _through_gateway(external)
    assert backend_path == "/api/projects"

    created = await authed_client.post(
        backend_path, json={"name": "addressing-contract"}
    )
    assert created.status_code == 200, (
        f"the schema sends callers to {external} to create a project; "
        f"that reached {created.status_code}"
    )
    project_id = created.json()["data"]["id"]

    listed = await authed_client.get(backend_path)
    assert listed.status_code == 200
    assert project_id in {p["id"] for p in listed.json()["data"]["data"]}

    # One `/api` less is the 1.0 team-projects collection — a different resource
    # with a different contract (it requires `team_id`), which is precisely why the
    # two cannot share an external URL.
    flattened = await authed_client.get("/projects")
    assert project_id not in flattened.text, (
        f"{_GATEWAY_PREFIX}/projects served the 2.0 project as well, so the two "
        "generations answer at one URL and one of them is unreachable"
    )


@pytest.mark.parametrize("page", ["/docs", "/redoc"])
def test_the_docs_page_finds_the_spec_through_the_gateway(
    raw_client: TestClient, page: str
) -> None:
    """The interactive docs load their spec from wherever they were opened.

    Naming it `/openapi.json` looks right and is not: opened at `<origin>/api/docs`
    a browser resolves that against the ORIGIN, lands outside the `/api/` forward,
    and gets the SPA's index.html with a 200 on it — a docs page that renders
    nothing and reports no error. A relative reference resolves against the page's
    own path instead, which is correct from both sides of the gateway.
    """
    external_page = _external_url(_published_schema(raw_client), page)
    served = _through_gateway(external_page)
    assert served is not None
    html = raw_client.get(served)
    assert html.status_code == 200

    refs = re.findall(r"""(?:url:\s*'|spec-url=")([^'"]*openapi\.json)""", html.text)
    assert refs, f"{page} names no spec at all"
    for ref in refs:
        resolved = urljoin(external_page, ref)
        forwarded = _through_gateway(resolved)
        assert forwarded is not None, (
            f"{page} points at {ref!r}, which from {external_page} resolves to "
            f"{resolved} — the gateway serves the SPA there, not the spec"
        )
        assert raw_client.get(forwarded).status_code == 200, (
            f"{page} points at {ref!r}, which reaches no spec"
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

    The other two blocks are pinned for the same reason: `_through_gateway` above
    is a model of this file, and a model nobody checks is just a second opinion.
    """
    conf = _NGINX_CONF.read_text()

    block = re.search(r"location\s+/api/\s*\{(?P<body>.*?)\n\s*\}", conf, re.DOTALL)
    assert block, "the /api forward disappeared from the frontend image's nginx"

    proxy_pass = re.search(r"proxy_pass\s+(?P<target>\S+);", block.group("body"))
    assert proxy_pass, "the /api location forwards nowhere"
    assert proxy_pass.group("target").endswith("/"), (
        "proxy_pass lost its trailing slash, so nginx no longer strips /api — "
        "the schema's published server is now wrong by one segment"
    )

    passthrough = re.search(
        r"location\s+~\s+(?P<pattern>\S+)\s*\{(?P<body>.*?)\n\s*\}", conf, re.DOTALL
    )
    assert passthrough, "the un-stripped terminal/app passthrough block is gone"
    assert passthrough.group("pattern") == _UNSTRIPPED_PASSTHROUGH.pattern, (
        "the regex location no longer matches what this test models: "
        f"nginx has {passthrough.group('pattern')}, "
        f"the model has {_UNSTRIPPED_PASSTHROUGH.pattern}"
    )
    assert not re.search(r"proxy_pass\s+\S+/;", passthrough.group("body")), (
        "the passthrough block gained a trailing slash, so it now strips a segment"
    )

    connector = re.search(
        r"location\s+/connector/\s*\{(?P<body>.*?)\n\s*\}", conf, re.DOTALL
    )
    assert connector, "the /connector forward disappeared"
    connector_target = re.search(r"proxy_pass\s+(?P<t>\S+);", connector.group("body"))
    assert connector_target and connector_target.group("t").endswith("/connector/"), (
        "the /connector block no longer forwards the path verbatim"
    )
