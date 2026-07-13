"""P3 device flow over HTTP (real app + DB): start → approve (bind owner, mint durable
token) → poll → the durable token identifies the device, and the device→project
assignment holds. A device is PURE COMPUTE (execution-architecture v3) — approval mints
NO agent. Also: the /agent WS rejects a bad token, and approve requires a logged-in
human.
"""

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

    # 3. connect — the logged-in human approves (binds owner + assigns the device to
    #    the project + names the compute node). No agent is minted: a device is pure
    #    compute (execution-architecture v3); the agent that runs on it is resolved per
    #    project/topic at turn time.
    connect = client.post(
        "/connector/connect",
        json={
            "device_code": code,
            "project_id": project["id"],
            "device_name": "alice-studio",
        },
        headers=_bearer(token),
    )
    assert connect.status_code == 200, connect.text
    approved = connect.json()
    device_id = approved["device_id"]
    # The device is pure compute — the approval response carries no agent identity.
    assert "agent_handle" not in approved
    # The human-chosen name won over the cli-proposed one (fixes an "unnamed" node).
    assert approved["device_name"] == "alice-studio"
    assert approved["project_id"] == project["id"]

    # 4. poll after approval → durable token + id the cli persists.
    done = client.post("/connector/auth/device/poll", json={"device_code": code})
    dd = done.json()
    assert dd["status"] == "approved"
    assert dd["device_id"] == device_id and dd["token"]


def test_proposed_name_lets_approval_page_prefill_hostname(client):
    # The cli posts this machine's hostname at start; the approval page reads it back
    # (by code) to prefill an editable default — never a blank "unnamed" field.
    start = client.post(
        "/connector/auth/device/start",
        json={"device_name": "Andys-MacBook-Pro-510"},
    )
    code = start.json()["device_code"]
    r = client.get(f"/connector/auth/device/proposed-name?code={code}")
    assert r.status_code == 200, r.text
    assert r.json()["device_name"] == "Andys-MacBook-Pro-510"
    # An unknown code yields null (no enumeration, no error).
    assert (
        client.get("/connector/auth/device/proposed-name?code=nope").json()[
            "device_name"
        ]
        is None
    )


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
