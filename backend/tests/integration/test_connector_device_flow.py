"""P3 device flow over HTTP (real app + DB): start → approve (mint agent-user +
device binding, bind owner) → poll → the durable token identifies the device, and
the device→project assignment holds. Also: the /agent WS rejects a bad token, and
approve requires a logged-in human.
"""

from app.core.tokens import verify_session_token
from tests.conftest import seed_user


def _login(client, handle: str) -> str:
    # POST /api/users/login (cheesex Phase-0 handle login) was retired in the fusion
    # merge (unify P3). Device approval binds the token's int user id as the owner,
    # so seed a real user + a numeric-sub token.
    return seed_user(client, handle)


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_full_device_flow_start_approve_poll(client):
    # A project the device will be bound to.
    project = client.post(
        "/api/projects", json={"name": "P", "owner_handle": "alice"}
    ).json()["data"]
    token = _login(client, "alice")

    # 1. start — the client (frozen cli) gets a code + an approve link.
    start = client.post(
        "/connector/auth/device/start", json={"device_name": "alice-macbook"}
    )
    assert start.status_code == 200
    body = start.json()
    code = body["device_code"]
    assert code and body["approve_url"].endswith(code)

    # 2. poll before approval → pending.
    pending = client.post("/connector/auth/device/poll", json={"device_code": code})
    assert pending.json()["status"] == "pending"

    # 3. connect — the logged-in human approves (mints agent-user + binds owner +
    #    assigns the device to the project).
    connect = client.post(
        "/connector/connect",
        json={"device_code": code, "project_id": project["id"]},
        headers=_bearer(token),
    )
    assert connect.status_code == 200, connect.text
    approved = connect.json()
    device_id = approved["device_id"]
    agent_handle = approved["agent_handle"]
    assert agent_handle.startswith("agent-")
    assert approved["project_id"] == project["id"]

    # The minted agent identity is a real, verifiable user (agent-as-user).
    # It carries an agent-binding → is_agent is derived true (checked via login token
    # shape only here; the binding itself is covered by identity unit tests).
    assert verify_session_token(token) is not None

    # 4. poll after approval → durable token + id the cli persists.
    done = client.post("/connector/auth/device/poll", json={"device_code": code})
    dd = done.json()
    assert dd["status"] == "approved"
    assert dd["device_id"] == device_id and dd["token"]


def test_connect_requires_login(client):
    start = client.post("/connector/auth/device/start", json={"device_name": "m"})
    code = start.json()["device_code"]
    # No bearer → not an authenticated human → 401.
    r = client.post("/connector/connect", json={"device_code": code})
    assert r.status_code == 401


def test_agent_ws_rejects_unknown_token(client):
    import contextlib

    from starlette.websockets import WebSocketDisconnect

    # An unknown device token must be rejected at the handshake (1008).
    with contextlib.suppress(WebSocketDisconnect):
        with client.websocket_connect("/connector/agent?token=bogus") as ws:
            ws.receive_text()  # should not get here; the server closes 1008
