"""Every write route says who may call it, on the route (app/api/write_access.py).

The declaration replaced a table of path regexes kept apart from the routes.
#370 flattened a prefix, every regex stopped matching, and the 芝士-only write
surface opened without a single failure. A declaration rides on the route, so a
rename carries it along; a route without one stops the app from starting.
"""

from __future__ import annotations

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

# The frozen list may only shrink. Lower this when you declare routes; raising
# it lets a new route skip the declaration, which is what the list exists to stop.
_CEILING = 409

# The routes only a 芝士 credential may call. Widening one to ROUTE_DECIDES lets
# a person's browser session through, so it is a decision, not a refactor:
# change this set in the same commit, on purpose.
_CHEESE_ONLY = {
    ("POST", "/topics/{topic_id}/webhook-token", "cheese_only_in_room"),
    ("POST", "/topics/{topic_id}/note", "cheese_only_in_room"),
    ("POST", "/topics/{topic_id}/lock", "cheese_only_in_room"),
    ("POST", "/topics/{topic_id}/unlock", "cheese_only_in_room"),
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


def test_the_frozen_list_does_not_grow() -> None:
    assert len(UNDECLARED) <= _CEILING, "declare the new route instead"
    # Ratchet: once the list shrinks, the ceiling follows it down.
    assert len(UNDECLARED) == _CEILING, f"lower _CEILING to {len(UNDECLARED)}"


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
