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
        "/projects", json={"name": "P", "owner_handle": "alice"}
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


def test_the_human_door_creates_the_hosted_subtype(client):
    """Enrollment identifies human-owned supply; visibility is chosen per topic."""
    from app.api.routes.connector import DbSession, get_device_service
    from app.domain.device.service import DeviceService
    from app.domain.device.supply import Supply, Visibility

    recorded: list[tuple[Supply, Visibility]] = []

    class Recording(DeviceService):
        async def approve(
            self, code_value, *, owner_user_id, supply, visibility, name=None
        ):
            recorded.append((supply, visibility))
            return await super().approve(
                code_value,
                owner_user_id=owner_user_id,
                supply=supply,
                visibility=visibility,
                name=name,
            )

    def _recording_service(db: DbSession) -> DeviceService:
        from app.domain.device.sql_repository import SqlDeviceRepository

        return Recording(SqlDeviceRepository(db))

    token = _login(client, "bob")
    code = client.post(
        "/connector/auth/device/start", json={"device_name": "bobs-desktop"}
    ).json()["device_code"]

    client.app.dependency_overrides[get_device_service] = _recording_service
    try:
        connect = client.post(
            "/connector/connect",
            json={"device_code": code},
            headers=_bearer(token),
        )
    finally:
        client.app.dependency_overrides.pop(get_device_service, None)

    assert connect.status_code == 200, connect.text
    assert recorded == [(Supply.self_hosted, Visibility.isolated)]


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


def test_a_device_attaching_wakes_the_cloud_topic_waiting_on_it(client, monkeypatch):
    """A Cloud topic holds its first message until its machine is enrolled AND
    connected. The connector attaching is usually the last of those two facts,
    and it must trigger delivery itself rather than wait for the next sweep tick
    (53 s of a 4½-minute first turn on dev, 2026-09-02)."""
    owner = _login(client, "erin")
    code = client.post(
        "/connector/auth/device/start", json={"device_name": "erins-cloud-box"}
    ).json()["device_code"]
    connect = client.post(
        "/connector/connect", json={"device_code": code}, headers=_bearer(owner)
    )
    assert connect.status_code == 200, connect.text
    poll = client.post("/connector/auth/device/poll", json={"device_code": code}).json()
    device_id, device_token = poll["device_id"], poll["token"]

    woken: list[str] = []

    class Wakeup:
        async def wake_device(self, connected_device_id: str) -> None:
            woken.append(connected_device_id)

    class Chat:
        async def recover_sessions(self, connected_device_id: str) -> int:
            return 0

    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())
    monkeypatch.setattr("app.api.deps.get_cloud_wakeup", lambda: Wakeup())

    with client.websocket_connect(f"/connector/agent?token={device_token}") as ws:
        ws.close()

    assert woken == [device_id]


def test_agent_ws_does_not_park_a_session_idle_in_transaction(client, monkeypatch):
    """#356 regression, against a real Postgres.

    The device control channel stays connected for the machine's whole uptime, and
    its token check (``verify_token`` → the ``device_team`` read) runs on a
    ``Depends(get_db)`` session. A get_db session injected into a WebSocket route is
    finalized only when the socket CLOSES — so unless the handler ends that read
    transaction before parking in its receive loop, the connection sits
    ``idle in transaction`` for hours, holding an AccessShareLock on ``device_team``.
    That lock made an ``ALTER TABLE`` (ACCESS EXCLUSIVE) on the device tables queue
    behind it until it timed out → site-wide brownout. Here we enrol a real device,
    open the control channel, and assert no such parked transaction exists while it
    is connected."""
    import asyncio

    from sqlalchemy import text

    # Enrol a device the human-flow way so the durable token is real and its lookup
    # runs the exact device_team read the leak parked.
    owner = _login(client, "dave")
    code = client.post(
        "/connector/auth/device/start", json={"device_name": "daves-box"}
    ).json()["device_code"]
    connect = client.post(
        "/connector/connect", json={"device_code": code}, headers=_bearer(owner)
    )
    assert connect.status_code == 200, connect.text
    poll = client.post("/connector/auth/device/poll", json={"device_code": code}).json()
    device_id = poll["device_id"]
    device_token = poll["token"]
    assert device_token

    recovered: list[str] = []

    class Chat:
        async def recover_sessions(self, connected_device_id: str) -> int:
            recovered.append(connected_device_id)
            return 0

    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())

    async def _parked_on_device_team() -> int:
        # Superuser test role → pg_stat_activity exposes other backends' query text,
        # so this counts sessions frozen mid-`device_team` read (the leak's fingerprint,
        # matching the 2.8h idle-in-transaction found on dev).
        async with client.test_factory() as session:  # type: ignore[attr-defined]
            n = await session.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND state = 'idle in transaction' "
                    "AND query ILIKE '%device_team%'"
                )
            )
            return int(n or 0)

    # Baseline: nothing parked before the socket exists.
    assert asyncio.run(_parked_on_device_team()) == 0

    with client.websocket_connect(f"/connector/agent?token={device_token}") as ws:
        # The handshake has completed → the handler ran verify_token and is parked in
        # its receive loop. With the fix its auth transaction is already committed;
        # without it the connection is idle-in-transaction on the device_team read.
        parked = asyncio.run(_parked_on_device_team())
        ws.close()

    assert parked == 0, (
        f"the device control channel left {parked} session(s) idle-in-transaction on "
        "the device_team read — the #356 leak that blocks device-table migrations"
    )
    assert recovered == [device_id]
