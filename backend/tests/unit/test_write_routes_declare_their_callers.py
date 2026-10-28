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
    ROUTE_DECIDES,
    Can,
    refuse_unsealed_writes,
    seal,
    violations,
    write_routes,
)
from app.core.errors import register_exception_handlers
from app.domain.project_permissions import AGENT_ONLY, Permission
from app.main import app

# The routes only an AI teammate may call: each needs a permission no person's
# role holds. Moving one onto another declaration lets a person's browser
# session through, so it is a decision, not a refactor: change this set in the
# same commit, on purpose.
_AGENTS_ONLY = {
    ("POST", "/topics/{topic_id}/webhook-token", "can mint_webhook_token"),
    ("POST", "/topics/{topic_id}/note", "can note_own_threads"),
    ("POST", "/topics/{topic_id}/session-records", "can record_machine_status"),
}


def test_the_app_has_no_write_route_without_a_declaration() -> None:
    assert violations(app) == []


def test_the_surface_only_ai_teammates_call_is_the_one_decided() -> None:
    agents_only = {f"can {p}" for p in AGENT_ONLY}
    found = {
        (r.method, r.path, r.declared[0])
        for r in write_routes(app)
        if r.declared and r.declared[0] in agents_only
    }
    assert found == _AGENTS_ONLY


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

    found = violations(_app(router))
    assert len(found) == 1
    assert found[0].startswith("POST /things/{thing_id} ")


def test_a_router_declares_for_its_routes_and_a_route_declares_once() -> None:
    declared = APIRouter(prefix="/ok", dependencies=[ROUTE_DECIDES])

    @declared.delete("/{thing_id}")
    async def remove(thing_id: str) -> None: ...

    assert violations(_app(declared)) == []

    twice = APIRouter(prefix="/twice", dependencies=[ROUTE_DECIDES])

    @twice.post("/{topic_id}", dependencies=[Can(Permission.speak, at="topic_id")])
    async def both(topic_id: str) -> None: ...

    [found] = violations(_app(twice))
    assert "can speak and route_decides" in found


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
    router = APIRouter(prefix="/topics", dependencies=[ROUTE_DECIDES])

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
