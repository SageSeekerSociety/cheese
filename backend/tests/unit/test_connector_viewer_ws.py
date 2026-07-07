"""In-process integration test for the 现场 viewer WebSocket route (Act 2 step 4).

Drives a device ``/agent`` socket and a browser viewer socket together over real
ASGI plumbing (Starlette ``TestClient``), proving the full relay against the frozen
web-claude wire: first-resize → ``screen.subscribe`` at the real size, device
``screen.data`` → viewer bytes, viewer keystrokes → ``screen.input``, viewer mode →
``viewerLevel``, and detach → ``viewerLevel=passive`` + ``screen.unsubscribe``. No DB,
no device, no network.
"""

import base64
from functools import partial

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agent.hub import DeviceHub, HubScreen
from app.api.routes.connector_agent import build_agent_router
from app.api.routes.connector_viewer import build_viewer_router
from app.domain.device import DeviceService, InMemoryDeviceRepository


async def _allow(screen: HubScreen, websocket: object) -> bool:
    return True


async def _deny(screen: HubScreen, websocket: object) -> bool:
    return False


def _make_app(authorizer=_allow) -> tuple[FastAPI, DeviceService, DeviceHub]:  # type: ignore[no-untyped-def]
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    app = FastAPI()
    app.include_router(build_agent_router(svc, hub))
    app.include_router(build_viewer_router(svc, hub, authorizer))
    return app, svc, hub


async def _enroll(svc: DeviceService) -> tuple[str, str]:
    code = await svc.start("m")
    device = await svc.approve(code, actor_user_id=1)
    return device.token, device.device_id


def _open(hub: DeviceHub, device_id: str, command: list[str], source: str):  # type: ignore[no-untyped-def]
    return partial(hub.open_screen, device_id, command, source, project_id=7, agent_user_id=99)


def test_full_viewer_relay() -> None:
    app, svc, hub = _make_app()
    with TestClient(app) as client:
        token, device_id = client.portal.call(_enroll, svc)
        with client.websocket_connect("/agent", headers={"X-Cheese-Session": token}) as dev:
            assert dev.receive_json()["t"] == "welcome"
            dev.send_json({"t": "hello", "v": 1})
            screen = client.portal.call(_open(hub, device_id, ["claude"], "SRC"))
            assert dev.receive_json()["t"] == "session.create"

            with client.websocket_connect(f"/connector/session/{screen.sid}/screen") as view:
                # First resize drives screen.subscribe at the viewer's real size.
                view.send_json({"type": "resize", "cols": 90, "rows": 30})
                sub = dev.receive_json()
                assert sub["t"] == "screen.subscribe"
                assert (sub["cols"], sub["rows"]) == (90, 30)

                # Device screen bytes fan out to the viewer.
                payload = b"\x1b[2Jhello-screen"
                client.portal.call(
                    hub.on_device_message,
                    device_id,
                    {"t": "screen.data", "sid": screen.sid, "data": base64.b64encode(payload).decode()},
                )
                assert view.receive_bytes() == payload

                # Viewer keystrokes become screen.input (base64).
                view.send_bytes(b"ls\r")
                inp = dev.receive_json()
                assert inp["t"] == "screen.input"
                assert base64.b64decode(inp["data"]) == b"ls\r"

                # Viewer mode becomes the viewerLevel variable.
                view.send_json({"type": "control", "level": "full"})
                lvl = dev.receive_json()
                assert lvl["t"] == "var.set"
                assert lvl["name"] == "viewerLevel"
                assert lvl["value"] == "full"

            # Detaching the last viewer: passive + unsubscribe.
            passive = dev.receive_json()
            assert passive["t"] == "var.set" and passive["value"] == "passive"
            unsub = dev.receive_json()
            assert unsub["t"] == "screen.unsubscribe"


def test_malformed_resize_does_not_crash_viewer() -> None:
    app, svc, hub = _make_app()
    with TestClient(app) as client:
        token, device_id = client.portal.call(_enroll, svc)
        with client.websocket_connect("/agent", headers={"X-Cheese-Session": token}) as dev:
            dev.receive_json()  # welcome
            screen = client.portal.call(_open(hub, device_id, ["x"], "SRC"))
            dev.receive_json()  # session.create
            with client.websocket_connect(f"/connector/session/{screen.sid}/screen") as view:
                # Garbage dimensions must not kill the socket — subscribe falls back to defaults.
                view.send_json({"type": "resize", "cols": "abc", "rows": None})
                sub = dev.receive_json()
                assert sub["t"] == "screen.subscribe"
                assert (sub["cols"], sub["rows"]) == (120, 32)


def test_viewer_unknown_screen_is_rejected() -> None:
    app, _svc, _hub = _make_app()
    with (
        TestClient(app) as client,
        pytest.raises(WebSocketDisconnect),
        client.websocket_connect("/connector/session/nope/screen") as view,
    ):
        view.receive_bytes()


def test_viewer_authorization_is_enforced() -> None:
    app, svc, hub = _make_app(authorizer=_deny)
    with TestClient(app) as client:
        _token, device_id = client.portal.call(_enroll, svc)
        screen = client.portal.call(_open(hub, device_id, ["x"], "SRC"))
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(f"/connector/session/{screen.sid}/screen") as view,
        ):
            view.receive_bytes()


def test_viewer_default_policy_denies() -> None:
    # A router built without an explicit authorizer denies by default — a screen is
    # never world-viewable by omission.
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    app = FastAPI()
    app.include_router(build_agent_router(svc, hub))
    app.include_router(build_viewer_router(svc, hub))  # no authorizer → _deny
    with TestClient(app) as client:
        _token, device_id = client.portal.call(_enroll, svc)
        screen = client.portal.call(_open(hub, device_id, ["x"], "SRC"))
        with (
            pytest.raises(WebSocketDisconnect),
            client.websocket_connect(f"/connector/session/{screen.sid}/screen") as view,
        ):
            view.receive_bytes()
