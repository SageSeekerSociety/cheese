"""P3 device flow over HTTP (real app + DB): start → approve (bind owner, mint durable
token) → poll → the durable token identifies the device, and the device→project
assignment holds. A device is PURE COMPUTE (execution-architecture v3) — approval mints
NO agent. Also: the /agent WS rejects a bad token, and approve requires a logged-in
human.
"""

import asyncio
import socket

import httpx
import pytest
import uvicorn

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


def test_device_code_is_committed_before_start_http_response():
    """A second TCP request must find a code after the first receives its 200."""
    from app.core.db import engine, get_db
    from app.main import app

    async def check() -> None:
        commit_entered = asyncio.Event()
        release_commit = asyncio.Event()
        response_sent = asyncio.Event()

        async def gated_get_db():
            provider = get_db()
            session = await anext(provider)
            original_commit = session.commit

            async def held_commit():
                if not commit_entered.is_set():
                    commit_entered.set()
                    await release_commit.wait()
                await original_commit()

            session.commit = held_commit
            try:
                yield session
            except BaseException as exc:
                try:
                    await provider.athrow(exc)
                except StopAsyncIteration:
                    pass
                raise
            else:
                try:
                    await anext(provider)
                except StopAsyncIteration:
                    pass

        async def observed_app(scope, receive, send):
            async def observed_send(message):
                await send(message)
                if (
                    scope["type"] == "http"
                    and scope["path"] == "/connector/auth/device/start"
                    and message["type"] == "http.response.body"
                    and not message.get("more_body", False)
                ):
                    response_sent.set()

            await app(scope, receive, observed_send)

        app.dependency_overrides[get_db] = gated_get_db
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(
            uvicorn.Config(observed_app, log_level="error", lifespan="on")
        )
        server_task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            async with asyncio.timeout(20):
                while not server.started:
                    await asyncio.sleep(0.01)

            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{sock.getsockname()[1]}", timeout=10
            ) as http:
                start_task = asyncio.create_task(
                    http.post(
                        "/connector/auth/device/start", json={"device_name": "probe"}
                    )
                )
                try:
                    await asyncio.wait_for(commit_entered.wait(), timeout=10)
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(response_sent.wait(), timeout=0.5)
                    assert not start_task.done()
                finally:
                    release_commit.set()

                start = await asyncio.wait_for(start_task, timeout=10)
                assert start.status_code == 200, start.text
                code = start.json()["device_code"]
                poll = await http.post(
                    "/connector/auth/device/poll", json={"device_code": code}
                )
                assert poll.status_code == 200, poll.text
                assert poll.json()["status"] == "pending"
        finally:
            release_commit.set()
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=15)
            app.dependency_overrides.pop(get_db, None)
            await engine.dispose()

    asyncio.run(check())


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


@pytest.mark.parametrize("member", [False, True])
def test_project_binding_requires_membership_before_approval(client, member):
    owner = _login(client, "binding-owner")
    project = client.post(
        "/projects",
        json={"name": "Private project", "owner_handle": "binding-owner"},
        headers=_bearer(owner),
    ).json()["data"]
    token = _login(client, "device-owner")
    if member:
        added = client.post(
            f"/projects/{project['id']}/members",
            json={"user_handle": "device-owner"},
            headers=_bearer(owner),
        )
        assert added.status_code == 200, added.text
    code = client.post(
        "/connector/auth/device/start", json={"device_name": "test-device"}
    ).json()["device_code"]
    response = client.post(
        "/connector/connect",
        json={"device_code": code, "project_id": project["id"]},
        headers=_bearer(token),
    )
    assert response.status_code == (200 if member else 403), response.text
    polled = client.post(
        "/connector/auth/device/poll", json={"device_code": code}
    ).json()
    assert polled["status"] == ("approved" if member else "pending")
    if not member:
        # A denied project binding must leave the enrollment usable.
        retry = client.post(
            "/connector/connect", json={"device_code": code}, headers=_bearer(token)
        )
        assert retry.status_code == 200, retry.text


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


def test_a_closed_device_link_records_who_hung_up(client, monkeypatch, caplog):
    """The link is meant to last the machine's uptime; when it ends, say so.

    On dev the whole fleet is replaced about once a minute and neither end
    recorded it: the close code was discarded here and the device's cli logs
    nothing (#1140). Without the code there is no way to tell a peer closing on
    purpose from the connection dropping under it.
    """
    import logging

    owner = _login(client, "quinn")
    code = client.post(
        "/connector/auth/device/start", json={"device_name": "quinns-box"}
    ).json()["device_code"]
    connect = client.post(
        "/connector/connect", json={"device_code": code}, headers=_bearer(owner)
    )
    assert connect.status_code == 200, connect.text
    approved = client.post(
        "/connector/auth/device/poll", json={"device_code": code}
    ).json()
    device_token, device_id = approved["token"], approved["device_id"]

    class Chat:
        async def recover_sessions(self, connected_device_id: str) -> int:
            return 0

    class Wakeup:
        async def wake_device(self, connected_device_id: str) -> None:
            return None

    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: Chat())
    monkeypatch.setattr("app.api.deps.get_cloud_wakeup", lambda: Wakeup())

    with caplog.at_level(logging.INFO, logger="app.api.routes.connector"):
        with client.websocket_connect(f"/connector/agent?token={device_token}") as ws:
            ws.close()

    closed = [
        record.getMessage()
        for record in caplog.records
        if "device link closed" in record.getMessage()
    ]
    assert len(closed) == 1, [r.getMessage() for r in caplog.records]
    assert device_id in closed[0]
    # The code is what names the initiator, and the age is what tells a link
    # that died young from one that lasted.
    assert "code=" in closed[0]
    assert "after=" in closed[0]


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
