"""In-process integration test for the device ``/agent`` WebSocket route (step 3).

Uses Starlette's ``TestClient`` — real ASGI WebSocket plumbing, no network, no DB.
``client.portal.call`` runs the async ``DeviceService`` / ``DeviceHub`` in the app's
event loop (standing in for enrollment and the orchestrator opening a screen), so
the whole device channel is exercised: token auth, welcome handshake, inbound
dispatch, and server→device delivery.
"""

from functools import partial

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agent.hub import PROTOCOL_VERSION, DeviceHub
from app.api.routes.connector_agent import build_agent_router
from app.domain.device import DeviceService, InMemoryDeviceRepository


def _make_app() -> tuple[FastAPI, DeviceService, DeviceHub]:
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    app = FastAPI()
    app.include_router(build_agent_router(svc, hub))
    return app, svc, hub


async def _enroll(svc: DeviceService) -> tuple[str, str]:
    code = await svc.start("laptop")
    device = await svc.approve(code, actor_user_id=1)
    return device.token, device.device_id


def test_agent_ws_full_device_channel() -> None:
    app, svc, hub = _make_app()
    with TestClient(app) as client:
        token, device_id = client.portal.call(_enroll, svc)

        with client.websocket_connect("/agent", headers={"X-Cheese-Session": token}) as ws:
            # 1. Server greets with the version handshake.
            assert ws.receive_json() == {"t": "welcome", "v": PROTOCOL_VERSION}
            assert hub.is_online(device_id)

            # 2. Device announces itself.
            ws.send_json({"t": "hello", "v": PROTOCOL_VERSION})

            # 3. The orchestrator opens a screen → device receives session.create.
            screen = client.portal.call(
                partial(hub.open_screen, device_id, ["claude"], "CHEESELET", project_id=1, agent_user_id=1)
            )
            create = ws.receive_json()
            assert create["t"] == "session.create"
            assert create["sid"] == screen.sid
            assert create["screen"] == screen.token
            assert create["source"] == "CHEESELET"

            # 4. Device pushes a variable → the hub records it (inbound dispatch works).
            ws.send_json({"t": "var.push", "sid": screen.sid, "name": "busy", "value": True})
            # Force ordering: open a second screen and read it; by the time that
            # round-trips, the earlier var.push has been processed.
            screen2 = client.portal.call(
                partial(hub.open_screen, device_id, ["top"], "X", project_id=1, agent_user_id=1)
            )
            assert ws.receive_json()["sid"] == screen2.sid
            assert screen.vars.get("busy") is True

    # 5. After the socket closes, the device is no longer online.
    assert not hub.is_online(device_id)


def test_agent_ws_rejects_unknown_token() -> None:
    app, _svc, _hub = _make_app()
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/agent", headers={"X-Cheese-Session": "garbage"}) as ws,
    ):
        ws.receive_json()


def test_agent_ws_rejects_missing_token() -> None:
    app, _svc, _hub = _make_app()
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/agent") as ws,
    ):
        ws.receive_json()
