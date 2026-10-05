"""b6fcc6362b79 on the rows a deployment already holds.

Every row that said where it belongs with a room and an optional task says it
with one conversation id afterwards: the task's when it had one, the room's
otherwise. A reader asking for a room's own line gets what the room said and
none of its task's, and the reverse; and removing a task removes what belonged
to it, as removing it did before.
"""

import asyncio
import uuid

import asyncpg

from tests.conftest import _PG_BASE, _admin_recreate_db
from tests.integration.test_task_conversation_migration import _alembic

BEFORE = "e7c41a9b2d58"
AFTER = "b6fcc6362b79"


async def _seed(conn) -> dict:
    ids = {
        "team": 9_000_004,
        "project": uuid.uuid4(),
        "room": uuid.uuid4(),
        "task": uuid.uuid4(),
    }
    await conn.execute(
        "INSERT INTO team (id, handle, name, intro, description, avatar_id,"
        " created_at, updated_at) VALUES ($1, 'one-column-team', 't', '', '', 0,"
        " now(), now())",
        ids["team"],
    )
    await conn.execute(
        "INSERT INTO projects (id, name, ai_mode, settings, team_id, created_at,"
        " updated_at) VALUES ($1, 'p', 'auto', '{}', $2, now(), now())",
        ids["project"],
        ids["team"],
    )
    await conn.execute(
        "INSERT INTO topics (id, project_id, title, kind, status, created_at,"
        " updated_at) VALUES ($1, $2, '房间', 'topic', 'active', now(), now())",
        ids["room"],
        ids["project"],
    )
    await conn.execute(
        "INSERT INTO tasks (id, project_id, room_id, title, status, created_at,"
        " updated_at) VALUES ($1, $2, $3, 'work', 'open', now(), now())",
        ids["task"],
        ids["project"],
        ids["room"],
    )
    for where, task in (("room", None), ("task", ids["task"])):
        await conn.execute(
            "INSERT INTO blocks (id, project_id, topic_id, task_id, kind,"
            " author_type, author, content, refs, created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, 'message', 'participant', 'alice', $5, '[]',"
            " now(), now())",
            uuid.uuid4(),
            ids["project"],
            ids["room"],
            task,
            f"said in the {where}",
        )
        await conn.execute(
            "INSERT INTO agent_turns (id, topic_id, task_id, continuation_id, author,"
            " content, is_resume, resendable, started_at)"
            " VALUES ($1, $2, $3, $1, 'alice', $4, false, true, now())",
            uuid.uuid4(),
            ids["room"],
            task,
            f"turn in the {where}",
        )
        await conn.execute(
            "INSERT INTO resource_usage (id, project_id, topic_id, task_id, model,"
            " input_tokens, output_tokens, total_tokens, cost_usd, kind,"
            " created_at, updated_at)"
            " VALUES ($1, $2, $3, $4, $5, 1, 1, 2, 0.5, 'llm', now(), now())",
            uuid.uuid4(),
            ids["project"],
            ids["room"],
            task,
            where,
        )
        await conn.execute(
            "INSERT INTO topic_progress (id, topic_id, task_id, items, created_at,"
            " updated_at) VALUES ($1, $2, $3, $4::json, now(), now())",
            uuid.uuid4(),
            ids["room"],
            task,
            f'[{{"subject": "{where}"}}]',
        )
        await conn.execute(
            "INSERT INTO webhook_tokens (id, topic_id, task_id, project_id, version,"
            " created_at, updated_at) VALUES ($1, $2, $3, $4, $5, now(), now())",
            uuid.uuid4(),
            ids["room"],
            task,
            ids["project"],
            1 if task is None else 2,
        )
        await conn.execute(
            "INSERT INTO deliveries (id, event_id, recipient_handle, dedup_key, type,"
            " payload, event_at, recorded_at, topic_id, task_id)"
            " VALUES ($1, $2, 'cheese', $3, 'agent', '{}', now(), now(), $4, $5)",
            uuid.uuid4(),
            uuid.uuid4(),
            where,
            ids["room"],
            task,
        )
        await conn.execute(
            "INSERT INTO local_fs_access (id, device_id, owner_user_id, path, key,"
            " mode, decision, reason, created_at, topic_id, task_id)"
            " VALUES ($1, 'd', 1, $2, 'k', 'read', 'allowed', 'r', now(), $3, $4)",
            uuid.uuid4(),
            where,
            ids["room"],
            task,
        )
    await conn.execute(
        "INSERT INTO native_inputs (id, project_id, topic_id, recipient_handle,"
        " harness, native_session_id, input_id, work_id, block_ids, seen_block_ids,"
        " registered_at, held_block_ids, released_block_ids)"
        " VALUES ($1, $2, $3, 'cheese', 'claude-code', 's', $1, $1, '[]', '[]',"
        " now(), '[]', '[]')",
        uuid.uuid4(),
        ids["project"],
        ids["task"],
    )
    await conn.execute(
        "INSERT INTO timed_deliveries (id, project_id, topic_id, recipient_handle,"
        " content, due_at, requested_at)"
        " VALUES ($1, $2, $3, 'cheese', 'later', now(), now())",
        uuid.uuid4(),
        ids["project"],
        ids["task"],
    )
    await conn.execute(
        "INSERT INTO agent_sessions (id, topic_id, conversation_id, agent_handle,"
        " harness, created_at, updated_at)"
        " VALUES ($1, $2, $3, 'cheese', 'claude-code', now(), now())",
        uuid.uuid4(),
        ids["room"],
        ids["task"],
    )
    return ids


async def _check(conn, ids: dict) -> None:
    room, task = ids["room"], ids["task"]

    async def by_conversation(sql: str) -> dict:
        return {row[0]: row[1] for row in await conn.fetch(sql)}

    assert await by_conversation("SELECT conversation_id, content FROM blocks") == {
        room: "said in the room",
        task: "said in the task",
    }
    assert await by_conversation(
        "SELECT conversation_id, content FROM agent_turns"
    ) == {room: "turn in the room", task: "turn in the task"}
    assert await by_conversation(
        "SELECT conversation_id, model FROM resource_usage"
    ) == {room: "room", task: "task"}
    assert await by_conversation(
        "SELECT conversation_id, items->0->>'subject' FROM topic_progress"
    ) == {room: "room", task: "task"}
    assert await by_conversation(
        "SELECT conversation_id, version FROM webhook_tokens"
    ) == {room: 1, task: 2}
    assert await by_conversation(
        "SELECT conversation_id, dedup_key FROM deliveries"
    ) == {room: "room", task: "task"}
    assert await by_conversation(
        "SELECT conversation_id, path FROM local_fs_access"
    ) == {room: "room", task: "task"}
    for table in ("native_inputs", "timed_deliveries", "agent_sessions"):
        assert await conn.fetchval(f"SELECT conversation_id FROM {table}") == task  # noqa: S608

    # Removing the task removes what belonged to it and nothing of the room's.
    await conn.execute("DELETE FROM tasks WHERE id = $1", task)
    for table in (
        "blocks",
        "agent_turns",
        "resource_usage",
        "topic_progress",
        "webhook_tokens",
        "agent_sessions",
    ):
        left = await conn.fetch(f"SELECT conversation_id FROM {table}")  # noqa: S608
        assert [row[0] for row in left] == ([] if table == "agent_sessions" else [room])


def test_every_row_says_its_conversation_with_one_id():
    name = "one_conversation_column_" + uuid.uuid4().hex[:12]
    url = f"{_PG_BASE}/{name}"
    dsn = url.replace("+asyncpg", "")
    asyncio.run(_admin_recreate_db(name))

    async def run(step):
        conn = await asyncpg.connect(dsn)
        try:
            return await step(conn)
        finally:
            await conn.close()

    try:
        _alembic(url, BEFORE)
        ids = asyncio.run(run(_seed))
        _alembic(url, AFTER)
        asyncio.run(run(lambda conn: _check(conn, ids)))
    finally:

        async def drop():
            conn = await asyncpg.connect(_PG_BASE.replace("+asyncpg", "") + "/postgres")
            try:
                await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
            finally:
                await conn.close()

        asyncio.run(drop())
