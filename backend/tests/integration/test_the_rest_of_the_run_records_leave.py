"""What the first move left in the conversation leaves it too.

Memory changes, subagent starts and live-document reminders become run records;
so do turns that did not finish, except in a 支线, whose message says its reply
failed by reading them. A sentence for the agent that no turn has read yet is
still delivered: its line leaves the room, the sentence stays for the agent.
"""

import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.integration.conftest import in_thread, post_project

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_the_rest_of_the_run_records_leave.py"
    )
)


def _load():
    spec = importlib.util.spec_from_file_location("_mig_run_records_rest", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rest_of_the_platforms_running_leaves_the_room(client):
    project = post_project(client, json={"name": "P"}, owner="alice").json()["data"]
    room = project["root_topic_id"]
    thread = in_thread(client, room, "alice")
    now = datetime.now(UTC)
    rows = {
        "read": (
            room,
            "memory_changed",
            {"agent_notice": "重读", "consumed_turn": "t"},
        ),
        "unread": (room, "memory_changed", {"agent_notice": "重读"}),
        "subagent": (room, "subagent_start", {}),
        "failed": (room, "turn_failed", {}),
        "failed_in_thread": (thread, "turn_failed", {}),
        "needs_a_person": (room, "dispatch_unknown", {}),
    }
    ids = {key: uuid.uuid4() for key in rows}

    async def seed() -> None:
        async with client.test_factory() as s:
            for key, (where, kind, extra) in rows.items():
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, conversation_id, kind, "
                        "author_type, author, content, refs, meta, created_at, "
                        "updated_at) VALUES (:id, :p, :c, 'event', 'platform', "
                        "'system', :content, '[]', CAST(:meta AS json), :at, :at)"
                    ),
                    {
                        "id": ids[key],
                        "p": project["id"],
                        "c": where,
                        "content": key,
                        "meta": json.dumps({**extra, "event_type": kind}),
                        "at": now,
                    },
                )
            await s.commit()

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().move()

    async def move_and_read() -> tuple[dict, dict]:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(apply)
            await s.commit()
        async with client.test_factory() as s:
            said = {
                row.content: row.meta
                for row in await s.execute(
                    text("SELECT content, meta FROM blocks WHERE id = ANY(:ids)"),
                    {"ids": list(ids.values())},
                )
            }
            kept = {
                row.content: row
                for row in await s.execute(
                    text(
                        "SELECT content, conversation_id, kind, severity, meta "
                        "FROM run_records WHERE conversation_id = ANY(:rooms)"
                    ),
                    {"rooms": [uuid.UUID(room), uuid.UUID(thread)]},
                )
            }
        return said, kept

    asyncio.run(seed())
    said, kept = asyncio.run(move_and_read())

    # The room keeps what a person acts on, and a 支线 keeps its failure.
    shown = {key for key, meta in said.items() if meta.get("in_room") is not False}
    assert shown == {"failed_in_thread", "needs_a_person"}
    # The agent still gets its unread sentence; the room no longer shows it.
    assert said["unread"]["agent_notice"] == "重读"
    # Each of the rest is a run record of the same room.
    assert set(kept) == {"read", "unread", "subagent", "failed"}
    assert all(row.conversation_id == uuid.UUID(room) for row in kept.values())
    assert kept["failed"].severity == "error"
    assert "agent_notice" not in (kept["unread"].meta or {})
