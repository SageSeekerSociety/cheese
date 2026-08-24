"""P3 Phase B: the 现场 viewer WS authz, cheese-gate screen attribution, and the
「我的设备」management routes (real app + DB, the shared device_hub singleton)."""

import contextlib
import time
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.domain.agent.device_hub import HubScreen, device_hub
from tests.conftest import seed_user
from tests.integration.conftest import session_token


def _login(client, handle: str) -> str:
    # POST /api/users/login (cheesex Phase-0 handle login) was retired in the fusion
    # merge (unify P3). The 现场 viewer authz keys off the token's ``sub`` (= handle),
    # so mint a handle-scoped session token directly.
    return session_token(handle)


def _login_real(client, handle: str) -> str:
    # Device enrollment/management binds the token's int user id as the owner, so it
    # needs a real DB user + a numeric-sub token (not the handle-only token above).
    return seed_user(client, handle)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_screen(
    *, project_id: uuid.UUID | None, topic_id: uuid.UUID | None, handle: str
) -> HubScreen:
    """Register a screen straight on the singleton hub (no live device needed): the
    viewer route resolves it by sid and attribution by its token."""
    screen = HubScreen(
        sid="s" + uuid.uuid4().hex[:8],
        device_id="d" + uuid.uuid4().hex[:6],
        command=["claude"],
        token=uuid.uuid4().hex,
        agent_user_id=uuid.uuid4(),
        agent_handle=handle,
        project_id=project_id,
        topic_id=topic_id,
    )
    device_hub._screens[screen.sid] = screen
    device_hub._by_screen_token[screen.token] = screen
    device_hub._device(screen.device_id).screens[screen.sid] = screen
    return screen


def _unregister(screen: HubScreen) -> None:
    device_hub._screens.pop(screen.sid, None)
    device_hub._by_screen_token.pop(screen.token, None)
    device_hub._devices.pop(screen.device_id, None)


# --- 现场 viewer authz --------------------------------------------------------


def test_project_member_may_watch_screen(client):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    alice = _login(client, "alice")
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]), topic_id=None, handle="agent-x"
    )
    try:
        url = f"/connector/session/{screen.sid}/screen?token={alice}"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
            # The first sized viewer attaches; poll the shared set for the effect.
            for _ in range(100):
                if screen.viewers:
                    break
                time.sleep(0.02)
            assert screen.viewers, "the authorized viewer should be attached"
    finally:
        _unregister(screen)


def test_outsider_cannot_watch_screen(client):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    bob = _login(client, "bob")  # logged in, but not in alice's project/topic
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]), topic_id=None, handle="agent-x"
    )
    try:
        url = f"/connector/session/{screen.sid}/screen?token={bob}"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(url) as ws:
                ws.receive_bytes()  # server closes 1008 before any data
    finally:
        _unregister(screen)


def test_unknown_screen_is_refused(client):
    alice = _login(client, "alice")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/connector/session/s-nope/screen?token={alice}"
        ) as ws:
            ws.receive_bytes()


# --- cheese-gate attribution wiring ------------------------------------------


def test_cheese_call_inside_screen_is_attributed_to_the_agent(client):
    """A cheese write carrying ``X-Cheese-Screen`` acts as the screen's agent-user
    (device agent-as-user), not the generic 芝士 nor the body's author."""
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "T"}
    ).json()["data"]
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]),
        topic_id=uuid.UUID(topic["id"]),
        handle="agent-macbook",
    )
    try:
        r = client.put(
            f"/topics/{topic['id']}/doc",
            json={"content": "# hi", "author": "someone-forged", "expected_version": 0},
            headers={"X-Cheese-Screen": screen.token},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["author"] == "agent-macbook"
    finally:
        _unregister(screen)


def test_cheese_call_without_screen_header_is_not_the_agent(client):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    topic = client.post(
        "/topics", json={"project_id": project["id"], "title": "T"}
    ).json()["data"]
    # No X-Cheese-Screen → the screen attribution never fires (author is the fallback).
    r = client.put(
        f"/topics/{topic['id']}/doc",
        json={"content": "# hi", "author": "human-alice", "expected_version": 0},
    )
    assert r.status_code == 200, r.text
    assert r.json()["data"]["author"] == "human-alice"


# --- 「我的设备」management CRUD ------------------------------------------------


def _enroll_device(client, owner_token: str, project_id: str | None = None) -> dict:
    start = client.post("/connector/auth/device/start", json={"device_name": "macbook"})
    code = start.json()["device_code"]
    body: dict = {"device_code": code}
    if project_id:
        body["project_id"] = project_id
    connect = client.post("/connector/connect", json=body, headers=_bearer(owner_token))
    assert connect.status_code == 200, connect.text
    return connect.json()


def test_my_devices_list_rename_and_unbind(client):
    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    alice = _login_real(client, "alice")
    enrolled = _enroll_device(client, alice, project_id=project["id"])
    device_id = enrolled["device_id"]

    # List — the owner sees their compute device. It is pure compute, so the view
    # carries no agent identity (agents show up per-screen, not per-device).
    listing = client.get("/connector/my/devices", headers=_bearer(alice))
    assert listing.status_code == 200, listing.text
    devices = listing.json()["devices"]
    assert any(d["device_id"] == device_id for d in devices)
    mine = next(d for d in devices if d["device_id"] == device_id)
    assert "agent_handle" not in mine
    assert project["id"] in mine["project_ids"]

    # Rename.
    renamed = client.patch(
        f"/connector/my/devices/{device_id}",
        json={"name": "alice-air"},
        headers=_bearer(alice),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "alice-air"

    # Unbind (delete).
    deleted = client.delete(
        f"/connector/my/devices/{device_id}", headers=_bearer(alice)
    )
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
    after = client.get("/connector/my/devices", headers=_bearer(alice)).json()
    assert all(d["device_id"] != device_id for d in after["devices"])


def test_only_owner_may_manage_a_device(client):
    alice = _login_real(client, "alice")
    enrolled = _enroll_device(client, alice)
    device_id = enrolled["device_id"]
    bob = _login_real(client, "bob")

    # Bob doesn't own it → rename/delete are forbidden, and it isn't in his list.
    r = client.patch(
        f"/connector/my/devices/{device_id}",
        json={"name": "hijack"},
        headers=_bearer(bob),
    )
    assert r.status_code == 403
    r = client.delete(f"/connector/my/devices/{device_id}", headers=_bearer(bob))
    assert r.status_code == 403
    assert (
        client.get("/connector/my/devices", headers=_bearer(bob)).json()["devices"]
        == []
    )


def test_my_devices_requires_login(client):
    # No bearer → not an authenticated human.
    with contextlib.suppress(Exception):
        r = client.get("/connector/my/devices")
        assert r.status_code == 401


def test_a_member_can_type_into_the_screen(client, monkeypatch):
    """A terminal you cannot type into is a viewer, not a terminal.

    The hub could already carry input to the device; nothing called it, so the
    pane was a mirror. This pins the whole path: a binary frame from an
    authorized viewer reaches the device as screen input.
    """
    sent: list[tuple[str, str, bytes]] = []

    async def _capture(device_id, sid, data):
        sent.append((device_id, sid, data))

    monkeypatch.setattr(device_hub, "viewer_input", _capture)

    project = client.post(
        "/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    alice = _login(client, "alice")
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]), topic_id=None, handle="agent-x"
    )
    try:
        url = f"/connector/session/{screen.sid}/screen?token={alice}"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
            for _ in range(100):
                if screen.viewers:
                    break
                time.sleep(0.02)
            ws.send_bytes(b"ls\r")
            for _ in range(100):
                if sent:
                    break
                time.sleep(0.02)
    finally:
        _unregister(screen)

    assert sent, "the keystroke never reached the device"
    assert sent[0][1] == screen.sid and sent[0][2] == b"ls\r"


def test_keystrokes_before_attaching_are_dropped(client, monkeypatch):
    """Without a subscription there is no pane on the device to type into."""
    sent: list = []

    async def _capture(device_id, sid, data):
        sent.append(data)

    monkeypatch.setattr(device_hub, "viewer_input", _capture)

    project = client.post(
        "/projects", json={"name": "P2", "owner_handle": "alice"}
    ).json()["data"]
    alice = _login(client, "alice")
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]), topic_id=None, handle="agent-y"
    )
    try:
        url = f"/connector/session/{screen.sid}/screen?token={alice}"
        with client.websocket_connect(url) as ws:
            ws.send_bytes(b"rm -rf /\r")  # no resize yet → never attached
            time.sleep(0.2)
    finally:
        _unregister(screen)

    assert sent == []
