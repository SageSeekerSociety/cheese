"""A machine that comes back is handed the grant set the platform holds now.

The machine keeps its own copy of its local-directory grants and enforces that
copy. A grant or a revocation made while it is away is recorded, and the owner
is told it takes effect when the machine next connects. Until the machine is
sent the current set, it goes on honouring whatever it held before, a
directory its owner has since taken away included.

Both ways a backend holds its machines are covered: through the connection
owner (a rolling backend, which learns of a connection from the owner's
snapshot) and in-process (the single-process deployment, where the socket
lands in the same process).
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from app import device_connection_app
from app.api.routes import connector
from app.core import background
from app.core.config import settings
from app.domain.agent.device_hub import device_hub
from app.domain.agent.device_hub_rpc import RemoteDeviceHub
from app.domain.agent.harness.claude_code import owner_login
from app.domain.device.models import DeviceRow, HostedDeviceRow
from app.domain.local_fs.enforcement import PushOutcome, push_grants
from app.domain.local_fs.paths import Platform
from app.domain.local_fs.records import GrantMode, GrantScope
from app.domain.local_fs.wiring import sql_local_directory_service
from app.domain.user.models import User
from tests.support import wire
from tests.support.hang import HANG_S

SECRET = "test-owner-secret"
MACHINE = "laptop"


class _Sessions:
    """Session recovery is not what these tests are about."""

    async def recover_sessions(self, device_id: str) -> int:
        return 0


async def _no_login_check(*_args) -> None:
    """Whether the machine's owner has logged in their own Claude Code is not
    what these tests are about either."""


@pytest.fixture
def hub(monkeypatch, db_factory):
    monkeypatch.setattr(owner_login, "refresh_on_connect", _no_login_check)
    monkeypatch.setattr(device_hub, "_devices", {})
    monkeypatch.setattr(device_hub, "_screens", {})
    monkeypatch.setattr(device_hub, "_by_screen_token", {})
    monkeypatch.setattr("app.api.deps.get_chat_service", lambda: _Sessions())
    monkeypatch.setattr("app.core.db.async_session_factory", db_factory)
    return device_hub


@pytest.fixture
async def rolling_backend(monkeypatch, hub):
    """A business backend that reaches its machines through the owner."""
    monkeypatch.setattr(settings, "device_connection_owner", True)
    monkeypatch.setattr(settings, "device_connection_secret", SECRET)
    monkeypatch.setattr(device_connection_app, "_executor_calls", {})
    monkeypatch.setattr(device_connection_app, "_release_draining", False)
    remote = RemoteDeviceHub(
        "http://owner",
        SECRET,
        transport=httpx.ASGITransport(app=device_connection_app.app),
    )
    # What `main.lifespan` does on a backend configured with an owner, whose
    # `device_hub` is then this remote hub everywhere it is imported.
    monkeypatch.setattr(connector, "device_hub", remote)
    remote.set_online_callback(connector.recover_business_state)
    await remote.start()
    yield remote
    await remote.close()
    await _recovery_finished()


async def _owner_of_the_machine(db_factory) -> int:
    now = datetime.now(UTC)
    async with db_factory() as session:
        user = User(
            username=f"owner{uuid.uuid4().hex[:8]}",
            email=f"{uuid.uuid4().hex[:8]}@example.test",
            created_at=now,
            updated_at=now,
        )
        session.add(user)
        await session.flush()
        session.add(
            DeviceRow(
                device_id=MACHINE,
                name=MACHINE,
                token=f"tok-{uuid.uuid4().hex}",
                owner_user_id=user.id,
                created_at=now,
            )
        )
        await session.flush()
        session.add(HostedDeviceRow(device_id=MACHINE, owner_user_id=user.id))
        await session.commit()
        return user.id


async def _grant(db_factory, link, owner: int, path: str) -> uuid.UUID:
    """What authorizing a directory does: record it, commit, push the set."""
    async with db_factory() as session:
        service = sql_local_directory_service(session)
        grant = await service.grant_directory(
            device_id=MACHINE,
            owner_user_id=owner,
            path=path,
            platform=Platform.LINUX,
            mode=GrantMode.READ,
            scope=GrantScope.USER,
        )
        await session.commit()
        outcome = await push_grants(service, link, MACHINE)
    assert outcome.reason == "device_offline", outcome
    return grant.id


async def _revoke(db_factory, link, owner: int, grant_id: uuid.UUID) -> PushOutcome:
    """What revoking does: record it, commit, push the set."""
    async with db_factory() as session:
        service = sql_local_directory_service(session)
        assert await service.revoke(grant_id, owner_user_id=owner) is not None
        await session.commit()
        return await push_grants(service, link, MACHINE)


async def _connect() -> wire.RecordingDevice:
    machine = wire.RecordingDevice()
    await device_hub.attach_device(MACHINE, machine)
    assert (await machine.sent.get())["t"] == "welcome"
    return machine


async def _grant_set(machine: wire.RecordingDevice) -> dict[str, Any]:
    while True:
        frame = await asyncio.wait_for(machine.sent.get(), HANG_S)
        if frame.get("t") == "localfs.grants":
            return frame


async def _answer(frame: dict[str, Any], **reply: Any) -> None:
    await device_hub.on_device_message(
        MACHINE, {"t": "localfs.grants.result", "id": frame["id"], **reply}
    )


async def _apply(frame: dict[str, Any]) -> None:
    await _answer(frame, value={"fingerprint": "fp", "applied": True})


def _paths(frame: dict[str, Any]) -> list[str]:
    return sorted(grant["path"] for grant in frame["value"]["grants"])


async def _recovery_finished() -> None:
    for _ in range(int(HANG_S / 0.01)):
        await asyncio.sleep(0.01)
        if background.inflight_count() == 0:
            return
    raise AssertionError("reconnect recovery never finished")


# -- through the connection owner -------------------------------------------


@pytest.mark.anyio
async def test_a_grant_made_while_away_reaches_the_machine_when_it_connects(
    rolling_backend, db_factory
) -> None:
    owner = await _owner_of_the_machine(db_factory)
    await _grant(db_factory, rolling_backend, owner, "/home/alice/Paper")

    machine = await _connect()
    frame = await _grant_set(machine)
    await _apply(frame)

    assert _paths(frame) == ["/home/alice/Paper"]


@pytest.mark.anyio
async def test_a_revoke_made_while_away_reaches_the_machine_when_it_reconnects(
    rolling_backend, db_factory
) -> None:
    owner = await _owner_of_the_machine(db_factory)
    paper = await _grant(db_factory, rolling_backend, owner, "/home/alice/Paper")
    await _grant(db_factory, rolling_backend, owner, "/home/alice/Notes")
    machine = await _connect()
    await _apply(await _grant_set(machine))
    await _recovery_finished()

    await device_hub.detach_device(MACHINE, machine)
    revoked = await _revoke(db_factory, rolling_backend, owner, paper)
    assert revoked.reason == "device_offline"
    machine = await _connect()
    frame = await _grant_set(machine)
    await _apply(frame)

    assert _paths(frame) == ["/home/alice/Notes"]


@pytest.mark.anyio
async def test_a_set_the_machine_refused_on_connect_is_sent_again_next_time(
    rolling_backend, db_factory, caplog
) -> None:
    owner = await _owner_of_the_machine(db_factory)
    await _grant(db_factory, rolling_backend, owner, "/home/alice/Paper")
    machine = await _connect()

    with caplog.at_level(logging.WARNING):
        await _answer(await _grant_set(machine), error="grant store is read-only")
        await _recovery_finished()
    assert any(
        MACHINE in record.getMessage() and "read-only" in record.getMessage()
        for record in caplog.records
    ), [record.getMessage() for record in caplog.records]

    await device_hub.detach_device(MACHINE, machine)
    machine = await _connect()
    frame = await _grant_set(machine)
    await _apply(frame)

    assert _paths(frame) == ["/home/alice/Paper"]


# -- in-process --------------------------------------------------------------


async def _connect_in_process() -> wire.RecordingDevice:
    # What the device socket does when this process holds the machines itself.
    machine = await _connect()
    await connector.recover_business_state(MACHINE)
    return machine


@pytest.mark.anyio
async def test_in_process_a_revoke_made_while_away_reaches_the_machine(
    hub, db_factory
) -> None:
    owner = await _owner_of_the_machine(db_factory)
    paper = await _grant(db_factory, hub, owner, "/home/alice/Paper")
    await _grant(db_factory, hub, owner, "/home/alice/Notes")
    machine = await _connect_in_process()
    first = await _grant_set(machine)
    await _apply(first)
    await _recovery_finished()
    assert _paths(first) == ["/home/alice/Notes", "/home/alice/Paper"]

    await device_hub.detach_device(MACHINE, machine)
    revoked = await _revoke(db_factory, hub, owner, paper)
    assert revoked.reason == "device_offline"
    machine = await _connect_in_process()
    frame = await _grant_set(machine)
    await _apply(frame)
    await _recovery_finished()

    assert _paths(frame) == ["/home/alice/Notes"]


@pytest.mark.anyio
async def test_a_machine_never_granted_a_directory_is_not_sent_a_set(
    hub, db_factory
) -> None:
    await _owner_of_the_machine(db_factory)

    machine = await _connect_in_process()
    await _recovery_finished()

    frames = [machine.sent.get_nowait() for _ in range(machine.sent.qsize())]
    assert [f for f in frames if f.get("t") == "localfs.grants"] == []
