"""A sandbox's request finds the session holding it from an index.

Every request a sandbox makes through its device link (`/machine/api/...`,
`/machine/llm/...`) starts by finding the session whose lease holds that
sandbox: the sessions whose ``work_lease ->> 'device_id'`` is the device it
came in on (`machine.caller.caller_of`). `ix_agent_sessions_lease_device`
indexes that expression, so the read is not a scan of every session.
"""

import re

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Engine

from app.domain.machine.caller import caller_of
from tests.integration.test_switch_pushes_first import _room

pytestmark = pytest.mark.anyio


async def test_finding_a_sandboxs_session_uses_the_lease_device_index(client):
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
    # The key is a literal, as the index spells it: a bound key would leave a
    # prepared statement's generic plan unable to use the index.
    assert "(agent_sessions.work_lease ->> 'device_id')" in sql, sql
    # The statement as sent, its device bound, over enough sessions on other
    # devices that reading them all would be the planner's choice without it.
    bound = re.sub(r"\$1(::VARCHAR)?", "CAST(:device AS varchar)", sql)
    async with client.test_factory() as db:
        await db.execute(
            text(
                "INSERT INTO agent_sessions (id, conversation_id, agent_handle,"
                " harness, work_lease, created_at, updated_at)"
                " SELECT gen_random_uuid(), :room, 'other-' || n, 'claude-code',"
                " json_build_object('device_id', 'device-' || n), now(), now()"
                " FROM generate_series(1, 5000) AS n"
            ),
            {"room": str(room.conversation_id)},
        )
        await db.execute(text("ANALYZE agent_sessions"))
        plan = "\n".join(
            (
                await db.execute(text("EXPLAIN " + bound), {"device": room.old_device})
            ).scalars()
        )

    assert "ix_agent_sessions_lease_device" in plan, plan
