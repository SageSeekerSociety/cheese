"""What a machine's own requests cost before they are answered.

- A connector polls its running sessions every few seconds; the select that
  answers it took 0.8 ms of Python to put together and was put together again
  on every poll (`agent.device_sessions.running_on`).
- A sandbox's request through its device link finds the session holding the
  sandbox, then made that session's credential by reading the same row again
  and the room the lease already names (`machine.caller.credential_of`).
"""

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.domain.agent import device_sessions
from tests.integration.test_machine_platform_api import _ask
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


async def test_a_poll_runs_the_select_it_put_together_before(client):
    room = await _room(client)
    ran = []

    async with client.test_factory() as db:
        execute = db.execute

        async def recorded(statement, *args, **kwargs):
            ran.append(statement)
            return await execute(statement, *args, **kwargs)

        db.execute = recorded  # type: ignore[method-assign]
        await device_sessions.running_on(db, room.old_device)
        await device_sessions.running_on(db, room.new_device)

    assert len(ran) == 2
    assert ran[0] is ran[1]


async def test_a_sandbox_request_reads_its_session_and_room_once(client, monkeypatch):
    monkeypatch.setattr(settings, "device_connection_secret", "owner-secret")
    room = await _room(client)
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    event.listen(Engine, "after_cursor_execute", record)
    try:
        answer = _ask(
            client, f"topics/{room.topic_id}/agent/control", device=room.old_device
        )
    finally:
        event.remove(Engine, "after_cursor_execute", record)

    assert answer.status_code == 200, answer.text
    sessions = [s for s in statements if "FROM agent_sessions" in s]
    assert len(sessions) == 1, sessions
    # Its lease names the room's resource: only the route the request names
    # looks the room up, under the session's credential.
    room_lookups = [s for s in statements if "inside.room_id" in s]
    assert len(room_lookups) == 1, room_lookups
