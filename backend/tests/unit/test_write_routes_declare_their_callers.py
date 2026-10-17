"""Every write route says who may call it, on the route (app/api/write_access.py).

The declaration replaced a table of path regexes kept apart from the routes.
#370 flattened a prefix, every regex stopped matching, and the 芝士-only write
surface opened without a single failure. A declaration rides on the route, so a
rename carries it along; a route without one stops the app from starting.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, WebSocket
from fastapi.testclient import TestClient

from app.api.write_access import (
    CHEESE_ONLY_IN_PROJECT,
    CHEESE_ONLY_IN_ROOM,
    ROUTE_DECIDES,
    refuse_unsealed_writes,
    seal,
    violations,
    write_routes,
)
from app.api.write_access_baseline import UNDECLARED
from app.core.errors import register_exception_handlers
from app.main import app

# The frozen list may only shrink, and where it started is read from git rather
# than kept here as a number: branches lower the list in parallel, and a
# constant in this file is one line all of them have to edit and conflict on.
_BASELINE = "backend/app/api/write_access_baseline.py"

# In order, the refs whose merge base with HEAD can be read where they exist:
# `origin/main` is what a clone of this repo has, and the other two cover one
# with no remote and one of a single branch.
_BASELINE_REFS = ("origin/main", "main", "HEAD~1")

# What the floor falls back to where no merge base can be read at all. The unit
# tests run on a depth-1 checkout, which has neither a parent nor `origin/main`,
# and a guard that waves through whatever it cannot read is off in exactly the
# run that gates a merge. Nobody owes an edit here: the comparison is `<=`, so
# lowering the list is not a change to this file.
_FLOOR_WITHOUT_HISTORY = 403

_ROOT = Path(__file__).resolve().parents[3]

# The routes only a 芝士 credential may call. Widening one to ROUTE_DECIDES lets
# a person's browser session through, so it is a decision, not a refactor:
# change this set in the same commit, on purpose.
_CHEESE_ONLY = {
    ("POST", "/topics/{topic_id}/webhook-token", "cheese_only_in_room"),
    ("POST", "/topics/{topic_id}/note", "cheese_only_in_room"),
    ("POST", "/projects/{project_id}/memory", "cheese_only_in_project"),
}


def test_the_app_has_no_write_route_without_a_declaration() -> None:
    assert violations(app, UNDECLARED) == []


def test_the_cheese_only_surface_is_the_one_decided() -> None:
    found = {
        (r.method, r.path, r.declared[0])
        for r in write_routes(app)
        if r.declared and r.declared[0].startswith("cheese_only")
    }
    assert found == _CHEESE_ONLY


def test_the_frozen_list_names_only_undeclared_routes_that_exist() -> None:
    undeclared = {(r.method, r.path) for r in write_routes(app) if not r.declared}
    stale = sorted(UNDECLARED - undeclared)
    assert not stale, (
        "declared, renamed or removed; delete these from "
        f"app/api/write_access_baseline.py: {stale}"
    )


def _frozen_list_size(source: str) -> int:
    """How many entries the `UNDECLARED` literal in `source` holds.

    Parsed rather than counted by lines: a path template long enough wraps its
    entry onto a second line.
    """
    for node in ast.parse(source).body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == "UNDECLARED" for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Call):
            [value] = value.args
        return len(set(ast.literal_eval(value)))
    raise AssertionError(f"no UNDECLARED in {_BASELINE}")


def _baseline_source() -> str | None:
    """The baseline file as the commit this branch started from had it.

    None when git can name no such commit and yields from no ref here either: a
    shallow checkout has neither the parent nor the refs a merge base needs, and
    git itself need not be installed. `_refuse_growth` then has a floor of its
    own rather than none.
    """
    try:
        for ref in _BASELINE_REFS:
            base = subprocess.run(
                ["git", "-C", str(_ROOT), "merge-base", "HEAD", ref],
                capture_output=True,
                text=True,
            )
            revs = base.stdout.split()
            if base.returncode or not revs:
                continue
            shown = subprocess.run(
                ["git", "-C", str(_ROOT), "show", f"{revs[0]}:{_BASELINE}"],
                capture_output=True,
                text=True,
            )
            if not shown.returncode:
                return shown.stdout
    except OSError:
        # No git on PATH, or what it is pointed at is not a repository.
        pass
    return None


def _refuse_growth(current: int, frozen: str | None) -> str | None:
    """Why a list of `current` entries may not stand, or None.

    The floor is the frozen list at the commit this branch started from — read
    from git, so lowering the list needs no edit in this file and two branches
    lowering it touch nothing in common. Where that commit cannot be read,
    `_FLOOR_WITHOUT_HISTORY` stands in for it, so the rule still answers rather
    than going quiet.
    """
    floor = _FLOOR_WITHOUT_HISTORY
    where = "the floor a checkout without history gets"
    if frozen is not None:
        fork_point = _frozen_list_size(frozen)
        if fork_point < floor:
            floor, where = fork_point, "where this branch started"
    if current <= floor:
        return None
    return (
        f"the frozen list holds {current} routes, over the {floor} allowed by "
        f"{where}: declare the new route instead of freezing it"
    )


def test_the_frozen_list_does_not_grow() -> None:
    reason = _refuse_growth(len(UNDECLARED), _baseline_source())
    assert reason is None, reason


def _a_frozen_list(size: int) -> str:
    """A baseline file standing in for one that froze `size` routes."""
    entries = "".join(f'        ("DELETE", "/x/{i}"),\n' for i in range(size))
    return (
        "UNDECLARED: frozenset[tuple[str, str]] = frozenset(\n"
        f"    {{\n{entries}    }}\n)\n"
    )


# Three entries, one of them wrapped the way a long path template is: a count
# that reads lines instead of the literal gets four.
_WRAPPED = """\
UNDECLARED: frozenset[tuple[str, str]] = frozenset(
    {
        ("DELETE", "/admin/admins/{target}"),
        ("PUT", "/users/{userId}/identity"),
        (
            "PUT",
            "/tasks/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
        ),
    }
)
"""


def test_a_list_that_grew_past_the_fork_point_is_refused() -> None:
    frozen = _a_frozen_list(403)
    assert _refuse_growth(404, frozen), "a route frozen since the fork point"
    assert _refuse_growth(403, frozen) is None
    assert _refuse_growth(402, frozen) is None
    # A fork point below the fallback is what the fork point is for: what the
    # branch started from decides, not the number standing in for it.
    assert _refuse_growth(403, _a_frozen_list(380))


def test_a_fork_point_that_cannot_be_read_is_not_a_guard_turned_off() -> None:
    assert _refuse_growth(_FLOOR_WITHOUT_HISTORY + 1, None)
    assert _refuse_growth(_FLOOR_WITHOUT_HISTORY, None) is None


def test_the_fork_points_list_is_counted_wherever_an_entry_wraps() -> None:
    assert _frozen_list_size(_WRAPPED) == 3


def test_the_parsed_file_matches_the_list_the_app_is_sealed_with() -> None:
    assert _frozen_list_size((_ROOT / _BASELINE).read_text(encoding="utf-8")) == len(
        UNDECLARED
    )


def _app(*routers: APIRouter, guarded: bool = False) -> FastAPI:
    built = FastAPI(dependencies=[Depends(refuse_unsealed_writes)] if guarded else None)
    register_exception_handlers(built)
    for router in routers:
        built.include_router(router)
    return built


def test_an_undeclared_write_route_is_refused() -> None:
    router = APIRouter(prefix="/things")

    @router.post("/{thing_id}")
    async def write(thing_id: str) -> None: ...

    @router.get("/{thing_id}")
    async def read(thing_id: str) -> None: ...

    found = violations(_app(router), frozenset())
    assert len(found) == 1
    assert found[0].startswith("POST /things/{thing_id} ")
    # The frozen list lets the same route through, by method and path.
    assert violations(_app(router), frozenset({("POST", "/things/{thing_id}")})) == []


def test_a_router_declares_for_its_routes_and_a_route_declares_once() -> None:
    declared = APIRouter(prefix="/ok", dependencies=[ROUTE_DECIDES])

    @declared.delete("/{thing_id}")
    async def remove(thing_id: str) -> None: ...

    assert violations(_app(declared), frozenset()) == []

    twice = APIRouter(prefix="/twice", dependencies=[ROUTE_DECIDES])

    @twice.post("/{topic_id}", dependencies=[CHEESE_ONLY_IN_ROOM])
    async def both(topic_id: str) -> None: ...

    [found] = violations(_app(twice), frozenset())
    assert "cheese_only_in_room and route_decides" in found


def test_a_cheese_only_route_refuses_a_caller_without_a_credential() -> None:
    router = APIRouter()

    @router.post("/topics/{topic_id}/x", dependencies=[CHEESE_ONLY_IN_ROOM])
    async def room(topic_id: str) -> dict:
        return {"reached": True}

    @router.post("/projects/{project_id}/x", dependencies=[CHEESE_ONLY_IN_PROJECT])
    async def project(project_id: str) -> dict:
        return {"reached": True}

    client = TestClient(_app(router))
    for path in ("/topics/t1/x", "/projects/p1/x"):
        for headers in ({}, {"x-cheese-token": "not-a-token"}):
            res = client.post(path, headers=headers)
            assert res.status_code == 401, (path, headers)
            assert res.json()["message"] == "invalid sandbox token"


def test_the_main_app_refuses_writes_seal_did_not_admit() -> None:
    assert any(
        getattr(dep, "dependency", None) is refuse_unsealed_writes
        for dep in app.router.dependencies
    )


def test_a_write_route_mounted_after_seal_is_refused_at_request_time() -> None:
    early = APIRouter(dependencies=[ROUTE_DECIDES])

    @early.post("/early")
    async def sealed() -> dict:
        return {"reached": True}

    built = _app(early, guarded=True)
    seal(built)
    late = APIRouter()

    @late.post("/late")
    async def unsealed() -> dict:
        return {"reached": True}

    @late.get("/late")
    async def read() -> dict:
        return {"reached": True}

    built.include_router(late)
    client = TestClient(built)
    assert client.post("/early").json() == {"reached": True}
    assert client.get("/late").json() == {"reached": True}
    refused = client.post("/late")
    assert refused.status_code == 403
    assert "write-access declaration" in refused.json()["message"]


def test_a_router_wide_declaration_leaves_its_sockets_and_reads_alone() -> None:
    for declaration in (ROUTE_DECIDES, CHEESE_ONLY_IN_ROOM):
        router = APIRouter(prefix="/topics", dependencies=[declaration])

        @router.websocket("/{topic_id}/socket")
        async def socket(websocket: WebSocket, topic_id: str) -> None:
            await websocket.accept()
            await websocket.send_text("hello")
            await websocket.close()

        @router.get("/{topic_id}/read")
        async def read(topic_id: str) -> dict:
            return {"reached": True}

        built = _app(router, guarded=True)
        seal(built)
        client = TestClient(built)
        with client.websocket_connect("/topics/t1/socket") as ws:
            assert ws.receive_text() == "hello"
        assert client.get("/topics/t1/read").json() == {"reached": True}
