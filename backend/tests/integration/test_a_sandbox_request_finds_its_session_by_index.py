"""A sandbox's request finds the session holding it from an index.

Every request a sandbox makes through its device link (`/machine/api/...`,
`/machine/llm/...`) starts by finding the sessions attached to the
environment on the device it came in on (`machine.caller.caller_of`).
`ix_environments_device_id` indexes that, so the read is not a scan of every
session.
"""

import re

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.domain.machine.caller import caller_of
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


async def test_finding_a_sandboxs_session_uses_the_environment_device_index(client):
    room = await _room(client)
    statements: list[tuple[str, object]] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append((" ".join(statement.split()), parameters))

    async with client.test_factory() as db:
        event.listen(Engine, "after_cursor_execute", record)
        try:
            caller = await caller_of(db, room.old_device, "/old/state")
        finally:
            event.remove(Engine, "after_cursor_execute", record)
    assert caller is not None and caller.session_id == str(room.session_id)

    (found,) = [s for s in statements if "FROM agent_sessions" in s[0]][:1]
    sql, _ = found
    assert "environments.device_id = " in sql, sql
    # The statement as sent, its device bound, over enough sessions on other
    # devices that reading them all would be the planner's choice without it.
    bound = re.sub(r"\$1(::VARCHAR)?", "CAST(:device AS varchar)", sql)
    async with client.test_factory() as db:
        await db.execute(
            text(
                "INSERT INTO agent_sessions (id, conversation_id, agent_handle,"
                " harness, created_at, updated_at)"
                " SELECT gen_random_uuid(), :room, 'other-' || n, 'claude-code',"
                " now(), now() FROM generate_series(1, 5000) AS n"
            ),
            {"room": str(room.conversation_id)},
        )
        # Each attached to an environment of its own on a device of its own.
        await db.execute(
            text(
                "INSERT INTO environments (id, topic_id, device_id, resource_id,"
                " room_resource_id, choice, created_at, updated_at)"
                " SELECT id, :room, 'device-' || agent_handle, 'r', 'r',"
                " '{}'::jsonb, now(), now()"
                " FROM agent_sessions WHERE agent_handle LIKE 'other-%'"
            ),
            {"room": str(room.topic_id)},
        )
        await db.execute(
            text(
                "INSERT INTO environment_attachments (id, environment_id,"
                " session_id, generation, acted_at)"
                " SELECT id, id, id, 'g', now()"
                " FROM agent_sessions WHERE agent_handle LIKE 'other-%'"
            )
        )
        for table in ("agent_sessions", "environments", "environment_attachments"):
            await db.execute(text(f"ANALYZE {table}"))
        plan = "\n".join(
            (
                await db.execute(text("EXPLAIN " + bound), {"device": room.old_device})
            ).scalars()
        )

    assert "ix_environments_device_id" in plan, plan
