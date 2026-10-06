"""The platform's running leaves the conversation for the 现场 and the admin page.

What a room keeps after the move is what people said and the work they need to
know about; what the platform did while running it is a run record, kept for a
month, and the platform's own errors belong to no room at all.
"""

import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from tests.integration.conftest import post_project

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_run_records_leave_the_conversation.py"
    )
)


def _load():
    spec = importlib.util.spec_from_file_location("_mig_run_records", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _room(client) -> tuple[uuid.UUID, uuid.UUID]:
    project = post_project(client, json={"name": "P"}).json()["data"]["id"]
    room = client.post("/topics", json={"project_id": project, "title": "讨论"})
    return uuid.UUID(project), uuid.UUID(room.json()["data"]["id"])


def test_the_platforms_running_moves_and_the_conversation_keeps_the_rest(client):
    project, room = _room(client)
    now = datetime.now(UTC)
    rows = {
        "retry": ("api_retry", "AI 服务请求失败，正在重试", now, {"seat": "cheese-a"}),
        "memory": ("memory_changed", "项目共享记忆：修改 1 条", now, {}),
        "error": ("backend_error", "后端报错：boom", now, {}),
        "untyped": (None, "输入已登记，发送结果正在核对；不会重复发送", now, {}),
        "storm": ("turn_queued", "项目同时运行的轮次已满，本轮正在排队", now, {}),
        "old": ("sandbox_asleep", "沙箱已休眠", now - timedelta(days=40), {}),
        "for_agent": ("memory_changed", "记忆被盖回", now, {"agent_notice": "重读"}),
        "rooted": ("api_retry", "有人在它下面开了支线", now, {}),
        "delivered": ("accept_done", "PR 已合并", now, {}),
    }
    stormy = datetime(2026, 10, 6, 2, tzinfo=UTC)
    ids = {key: uuid.uuid4() for key in rows}

    async def seed() -> None:
        async with client.test_factory() as s:
            for key, (kind, content, at, extra) in rows.items():
                meta = {**extra, **({"event_type": kind} if kind else {})}
                await s.execute(
                    text(
                        "INSERT INTO blocks (id, project_id, conversation_id, kind, "
                        "author_type, author, content, refs, meta, created_at, "
                        "updated_at) VALUES (:id, :p, :c, 'event', 'platform', "
                        "'system', :content, '[]', CAST(:meta AS json), :at, :at)"
                    ),
                    {
                        "id": ids[key],
                        "p": project,
                        "c": room,
                        "content": content,
                        "meta": json.dumps(meta),
                        "at": stormy if key == "storm" else at,
                    },
                )
            await s.execute(
                text(
                    "INSERT INTO threads (id, project_id, room_id, root_block_id, "
                    "reply_count, created_by, created_at) VALUES "
                    "(:id, :p, :r, :root, 0, 'alice', now())"
                ),
                {"id": uuid.uuid4(), "p": project, "r": room, "root": ids["rooted"]},
            )
            await s.commit()

    def apply(conn) -> None:
        with Operations.context(MigrationContext.configure(conn)):
            _load().move()

    async def move_and_read() -> tuple[set, dict]:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(apply)
            await s.commit()
        async with client.test_factory() as s:
            said = set(
                await s.scalars(
                    text("SELECT id FROM blocks WHERE id = ANY(:ids)"),
                    {"ids": list(ids.values())},
                )
            )
            kept = {
                row.id: row
                for row in await s.execute(
                    text(
                        "SELECT id, conversation_id, seat, kind, meta "
                        "FROM run_records WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(ids.values())},
                )
            }
        return said, kept

    asyncio.run(seed())
    said, kept = asyncio.run(move_and_read())
    by_key = {key: kept.get(ids[key]) for key in ids}

    # What people need stays where they read it.
    assert {key for key in ids if ids[key] in said} == {
        "for_agent",
        "rooted",
        "delivered",
    }
    # The platform's running is kept, under the same id, in the same room.
    assert by_key["retry"].conversation_id == room
    assert by_key["retry"].seat == "cheese-a"
    assert by_key["memory"].kind == "memory_changed"
    assert by_key["untyped"].kind == "delivery_checking"
    # An error is kept for the admin page and belongs to no room.
    assert by_key["error"].conversation_id is None
    assert by_key["error"].meta["conversation"] == str(room)
    # The storm's queue notices and anything past the retention are gone.
    assert by_key["storm"] is None
    assert by_key["old"] is None
