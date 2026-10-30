"""P3 Phase B: the 现场 viewer WS authz, cheese-gate screen attribution, and the
「我的设备」management routes (real app + DB, the shared device_hub singleton)."""

import asyncio
import contextlib
import json
import time
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from app.common.auth import create_access_token, verify_access_token
from app.domain.agent.device_hub import HubScreen, device_hub
from app.domain.user.repositories import UserRepository
from app.domain.user.sessions import SessionService
from tests.conftest import seed_user
from tests.integration.conftest import post_project, session_token


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


def _agent_identity(client, handle: str) -> None:
    """The agent user a real screen runs as: a user row carrying an agent binding,
    made the way the platform makes one. Without it the handle names nobody, and
    a room refuses it like any non-member."""
    import asyncio

    from app.domain.identity.services import IdentityService

    async def _make() -> None:
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            await IdentityService(session).ensure_agent_user(handle=handle, name=handle)
            await session.commit()

    asyncio.run(_make())


def _unregister(screen: HubScreen) -> None:
    device_hub._screens.pop(screen.sid, None)
    device_hub._by_screen_token.pop(screen.token, None)
    device_hub._devices.pop(screen.device_id, None)


# --- 现场 viewer authz --------------------------------------------------------


def test_project_member_may_watch_screen(client):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
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
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
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


def test_a_viewer_token_that_states_no_handle_resolves_through_its_user_id(client):
    """The 现场 gate reads the caller as a handle too, and the roster knows a
    name, never a user id: a token that names only the id has to be looked up.
    Comparing the roster against the id's digits denied the project's own owner.
    """
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    owner_id = verify_access_token(_login_real(client, "alice")).user_id
    assert owner_id is not None
    token = create_access_token(owner_id)  # older shape: no ``handle`` claim
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]), topic_id=None, handle="agent-x"
    )
    try:
        url = f"/connector/session/{screen.sid}/screen?token={token}"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
            for _ in range(100):
                if screen.viewers:
                    break
                time.sleep(0.02)
            assert screen.viewers, "the owner should be attached"
    finally:
        _unregister(screen)


def _signed_in_viewer(client, handle: str) -> tuple[int, uuid.UUID, str]:
    """A real sign-in for ``handle``, and the page's access token for it.

    ``session_token`` names a handle and no session, so there is nothing to
    revoke: only a token minted from a started session can be asked whether it
    still lasts."""
    holder: dict = {}

    async def seed() -> None:
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            users = UserRepository(db)
            user = await users.get_by_username(handle) or await users.create_user(
                username=handle, email=f"{handle}@example.com"
            )
            started = await SessionService(db).start(
                user.id, "password", ip="127.0.0.1", user_agent="test"
            )
            holder.update(user_id=user.id, sid=started.session_id)
            await db.commit()

    asyncio.run(seed())
    token = create_access_token(holder["user_id"], handle, sid=holder["sid"])
    return holder["user_id"], holder["sid"], token


def _revoke(client, user_id: int, sid: uuid.UUID) -> None:
    async def revoke() -> None:
        async with client.test_factory() as db:  # type: ignore[attr-defined]
            await SessionService(db).revoke(user_id, sid)
            await db.commit()

    asyncio.run(revoke())


def test_a_viewer_whose_sign_in_ended_stops_driving_the_screen(client, monkeypatch):
    """The handshake is not the last word: a sign-in revoked mid-view must end
    the view, or the pane stays drivable by a session the device list already
    says has ended. The viewer keeps sending frames for longer than the
    interval, so a check that rode on the frames themselves would come with the
    last of them, or never.

    The interval is 25s in production; its length is not what this pins, so it
    is shortened to keep the test quick."""
    monkeypatch.setattr("app.api.routes.connector.screen.VIEW_RECHECK_EVERY_S", 0.2)
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    user_id, sid, page = _signed_in_viewer(client, "alice")
    screen = _register_screen(
        project_id=uuid.UUID(project["id"]), topic_id=None, handle="agent-x"
    )
    try:
        url = f"/connector/session/{screen.sid}/screen?token={page}"
        with client.websocket_connect(url) as ws:
            ws.send_json({"type": "resize", "cols": 100, "rows": 30})
            for _ in range(100):
                if screen.viewers:
                    break
                time.sleep(0.02)
            assert screen.viewers, "the authorized viewer should be attached"

            _revoke(client, user_id, sid)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and screen.viewers:
                ws.send_json({"type": "resize", "cols": 100, "rows": 30})
                time.sleep(0.02)
            assert not screen.viewers, "the view outlived the sign-in that opened it"
            with pytest.raises(WebSocketDisconnect) as closed:
                ws.receive_bytes()
            assert closed.value.code == 4401
    finally:
        _unregister(screen)


def test_unknown_screen_is_refused(client):
    alice = _login(client, "alice")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/connector/session/s-nope/screen?token={alice}"
        ) as ws:
            ws.receive_bytes()


# --- Personal-team enrollment management -------------------------------------


def _enroll_device(client, owner_token: str, team_id: int | None = None) -> dict:
    start = client.post("/connector/auth/device/start", json={"device_name": "macbook"})
    code = start.json()["device_code"]
    body: dict = {"device_code": code}
    if team_id is not None:
        body["team_id"] = team_id
    connect = client.post("/connector/connect", json=body, headers=_bearer(owner_token))
    assert connect.status_code == 200, connect.text
    return connect.json()


def test_my_devices_list_rename_and_unbind(client):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    alice = _login_real(client, "alice")
    enrolled = _enroll_device(client, alice, team_id=project["team_id"])
    device_id = enrolled["device_id"]

    # List — the owner sees their compute device. It is pure compute, so the view
    # carries no agent identity (agents show up per-screen, not per-device).
    listing = client.get("/connector/my/devices", headers=_bearer(alice))
    assert listing.status_code == 200, listing.text
    devices = listing.json()["devices"]
    assert any(d["device_id"] == device_id for d in devices)
    mine = next(d for d in devices if d["device_id"] == device_id)
    assert "agent_handle" not in mine
    assert mine["team_id"] == project["team_id"]

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

    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
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

    project = post_project(client, json={"name": "P2"}, owner="alice").json()["data"]
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


def test_a_machine_learns_its_approval_was_withdrawn(client):
    """A machine unbound since its approval still holds its token; asked, the
    server says it no longer names a device, so connecting approves it again
    instead of installing a connector every dial of which is turned away."""
    alice = _login_real(client, "alice")
    start = client.post("/connector/auth/device/start", json={"device_name": "mac"})
    code = start.json()["device_code"]
    connect = client.post(
        "/connector/connect", json={"device_code": code}, headers=_bearer(alice)
    )
    assert connect.status_code == 200, connect.text
    token = client.post(
        "/connector/auth/device/poll", json={"device_code": code}
    ).json()["token"]
    device_id = connect.json()["device_id"]

    held = client.get(
        "/connector/device/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert held.status_code == 200 and held.json()["device_id"] == device_id

    client.delete(f"/connector/my/devices/{device_id}", headers=_bearer(alice))
    gone = client.get(
        "/connector/device/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert gone.status_code == 401
    assert (
        client.get(
            "/connector/device/me", headers={"Authorization": f"Bearer {''}"}
        ).status_code
        == 401
    )


def test_registered_login_refresh_is_visible_on_team_models(client, monkeypatch):
    """A registered subscription reports its status on Models; probing an
    execution device does not register a credential."""
    alice = _login_real(client, "alice")
    start = client.post("/connector/auth/device/start", json={"device_name": "macbook"})
    code = start.json()["device_code"]
    connect = client.post(
        "/connector/connect", json={"device_code": code}, headers=_bearer(alice)
    )
    assert connect.status_code == 200, connect.text
    device_id = connect.json()["device_id"]
    polled = client.post("/connector/auth/device/poll", json={"device_code": code})
    token = polled.json()["token"]
    listing = client.get("/connector/my/devices", headers=_bearer(alice)).json()
    mine = next(d for d in listing["devices"] if d["device_id"] == device_id)
    team_id = mine["team_id"]
    models_path = f"/connector/teams/{team_id}/model-connections"
    login_id = "default"
    status = {"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}

    async def exec_(device, argv, *, stdin, timeout):
        answer = {"installed": True, "logins": [{"id": login_id, "status": status}]}
        return {"exit": 0, "stdout": json.dumps(answer) + "\n", "stderr": ""}

    monkeypatch.setattr(device_hub, "is_online", lambda device: device == device_id)
    monkeypatch.setattr(device_hub, "exec", exec_)
    asked = client.post("/connector/model-login/refresh", json={"token": token})
    assert asked.status_code == 200, asked.text
    assert client.get(models_path, headers=_bearer(alice)).json()["connections"] == []

    authorization = client.post(
        f"/connector/teams/{team_id}/claude-login", headers=_bearer(alice)
    )
    assert authorization.status_code == 200, authorization.text
    registered = client.post(
        "/connector/model-login/register",
        json={
            "ticket": authorization.json()["ticket"],
            "transport_token": token,
            "computer_name": "macbook",
        },
    )
    assert registered.status_code == 200, registered.text
    login_id = registered.json()["login_id"]
    asked = client.post("/connector/model-login/refresh", json={"token": token})
    assert asked.status_code == 200, asked.text
    connections = client.get(models_path, headers=_bearer(alice)).json()["connections"]
    assert len(connections) == 1
    assert connections[0]["id"] == login_id
    assert connections[0]["device_id"] == device_id
    assert connections[0]["logged_in"] is True
    assert connections[0]["subscription_type"] == "max"
    listing = client.get("/connector/my/devices", headers=_bearer(alice)).json()
    mine = next(d for d in listing["devices"] if d["device_id"] == device_id)
    assert "claude_code" not in mine and "logins" not in mine

    bob = _login_real(client, "bob")
    theirs = client.post(
        f"/connector/teams/{team_id}/claude-logins/{login_id}/check",
        headers=_bearer(bob),
    )
    assert theirs.status_code == 403
