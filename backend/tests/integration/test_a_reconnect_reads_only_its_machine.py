"""A machine reconnecting reads the sessions on that machine, not every one.

Two reads run when a device connects (`connector/devices.py`), and both used
to read every session on the platform and sort them out in Python, with the
event loop held for it: production's stall sampler (`core/loop_stalls`) caught
each at 300-400 ms. Recovery asks for the placed sessions once per harness;
the closed-checkout sweep decoded every session's request and every room's
columns to find the leases on one machine."""

import asyncio
import json
import uuid

from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.domain.agent_session.services import AgentSessionService
from app.domain.room_task.checkouts import remove_closed_checkouts
from tests.integration.test_the_composer_shows_who_a_message_is_for import _channel


def _place(device: str) -> str:
    return json.dumps(
        {"device_id": device, "channel": "central", "resource_id": "r", "runtime": {}}
    )


def test_recovery_reads_the_placed_sessions_of_one_harness_on_one_machine(client):
    _project, _root, room = _channel(client)

    async def go():
        async with client.test_factory() as db:
            for handle, harness, device in (
                ("a", "claude", "dev-1"),
                ("b", "codex", "dev-1"),
                ("c", "claude", "dev-2"),
            ):
                await db.execute(
                    text(
                        "INSERT INTO agent_sessions (id, conversation_id,"
                        " agent_handle, harness, runtime_location, created_at,"
                        " updated_at) VALUES (gen_random_uuid(), :room, :h,"
                        " :harness, CAST(:loc AS json), now(), now())"
                    ),
                    {
                        "room": uuid.UUID(room),
                        "h": handle,
                        "harness": harness,
                        "loc": _place(device),
                    },
                )
            await db.flush()
            return await AgentSessionService(db).placed_sessions(
                harness="claude", device_id="dev-1"
            )

    placed = asyncio.run(go())
    assert [(row[3], row[4], row[6].machine) for row in placed] == [
        ("a", "claude", "dev-1")
    ]


def test_the_checkout_sweep_of_a_machine_reads_its_leases_not_whole_rows(client):
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.split()))

    async def go():
        event.listen(Engine, "after_cursor_execute", record)
        try:
            return await remove_closed_checkouts(
                client.test_factory, device_id="dev-gone"
            )
        finally:
            event.remove(Engine, "after_cursor_execute", record)

    assert asyncio.run(go()) == {"removed": 0, "kept": 0}
    (read,) = [
        s
        for s in statements
        if "FROM agent_sessions JOIN topics" in s and "topics.status !=" in s
    ]
    assert "agent_sessions.harness" not in read, read
    assert "work_lease ->>" in read, read
