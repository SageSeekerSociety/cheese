"""A compaction left saying "compacting" by a turn that has ended says it did
not finish; one whose turn is still running goes on saying it is compacting."""

import asyncio
import importlib.util
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

from app.domain.run_record.service import running
from tests.integration.conftest import post_project

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_compactions_of_ended_turns_are_over.py"
    )
)

_RUNNING = {
    "who": "platform",
    "i18n": {"content": {"key": "contextCompactRunning", "params": {}}},
    "state": "running",
    "detail": None,
    "severity": "info",
    "event_type": "context_compact",
    "detail_label": None,
}
_DONE = {
    **_RUNNING,
    "i18n": {"content": {"key": "contextCompactDone", "params": {}}},
    "state": "over",
}


def _load():
    spec = importlib.util.spec_from_file_location("_mig_compactions", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _apply(conn) -> None:
    with Operations.context(MigrationContext.configure(conn)):
        _load().close()


def test_a_compaction_of_an_ended_turn_is_over_and_a_live_ones_is_not(client):
    project = uuid.UUID(post_project(client, json={"name": "P"}).json()["data"]["id"])
    room = uuid.UUID(
        client.post(
            "/topics", json={"project_id": str(project), "title": "讨论"}
        ).json()["data"]["id"]
    )
    stopped_at = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    started_at = stopped_at - timedelta(minutes=20)
    turns = {
        "stopped": stopped_at,  # ended under a backend that lost its line
        "live": None,  # still compacting right now
        "unrecorded": "no row",  # a turn no row says is running
    }
    turn_ids = {key: uuid.uuid4() for key in turns}
    records = {
        "stopped": ("stopped", _RUNNING),
        "stopped_twice": ("stopped", _RUNNING),
        "stopped_done": ("stopped", _DONE),
        "live": ("live", _RUNNING),
        "unrecorded": ("unrecorded", _RUNNING),
    }
    record_ids = {key: uuid.uuid4() for key in records}

    async def seed() -> None:
        async with client.test_factory() as s:
            for key, stop in turns.items():
                if stop == "no row":
                    continue
                await s.execute(
                    text(
                        "INSERT INTO agent_turns (id, conversation_id, "
                        "continuation_id, author, content, is_resume, resendable, "
                        "started_at, delivered_at, stopped_at) VALUES "
                        "(:id, :c, :id, 'alice', 'hi', false, true, :start, "
                        ":start, :stop)"
                    ),
                    {"id": turn_ids[key], "c": room, "start": started_at, "stop": stop},
                )
            for key, (turn, meta) in records.items():
                content = (
                    "上下文整理好了"
                    if meta["state"] == "over"
                    else "对话太长，正在整理上下文；整理完会接着处理，期间不会回复"
                )
                await s.execute(
                    text(
                        "INSERT INTO run_records (id, project_id, conversation_id, "
                        "turn_id, seat, kind, severity, content, meta, created_at, "
                        "updated_at) VALUES (:id, :p, :c, :t, 'cheese-a', "
                        "'context_compact', 'info', :content, CAST(:meta AS json), "
                        ":at, :at)"
                    ),
                    {
                        "id": record_ids[key],
                        "p": project,
                        "c": room,
                        "t": turn_ids[turn],
                        "content": content,
                        "meta": json.dumps(meta),
                        "at": started_at,
                    },
                )
            await s.commit()

    async def migrate() -> None:
        async with client.test_factory() as s:
            await (await s.connection()).run_sync(_apply)
            await s.commit()

    async def read() -> tuple[dict, dict]:
        async with client.test_factory() as s:
            rows = {
                row.id: row
                for row in await s.execute(
                    text(
                        "SELECT id, content, severity, meta, updated_at "
                        "FROM run_records WHERE id = ANY(:ids)"
                    ),
                    {"ids": list(record_ids.values())},
                )
            }
            still = {
                key: await running(s, tid, "context_compact")
                for key, tid in turn_ids.items()
            }
        return {key: rows[rid] for key, rid in record_ids.items()}, still

    asyncio.run(seed())
    asyncio.run(migrate())
    after, still_running = asyncio.run(read())

    # Every line of an ended turn says the compaction did not finish, and why.
    for key in ("stopped", "stopped_twice", "unrecorded"):
        row = after[key]
        assert row.content == "上下文整理没有完成"
        assert row.severity == "warn"
        assert row.meta["state"] == "over"
        assert row.meta["detail"] == "会话在整理完成前结束了"
        assert row.meta["i18n"]["content"]["key"] == "contextCompactFailed"
        assert row.meta["i18n"]["detail"]["key"] == "contextCompactSessionEnded"
        assert row.meta["i18n"]["detail_label"]["key"] == "labelReason"
    # As of when its turn stopped.
    assert datetime.fromisoformat(after["stopped"].meta["at"]) == stopped_at
    assert still_running["stopped"] == []
    assert still_running["unrecorded"] == []

    # A compaction that finished keeps saying it finished.
    assert after["stopped_done"].content == "上下文整理好了"
    assert after["stopped_done"].meta["i18n"]["content"]["key"] == "contextCompactDone"

    # The live turn is still compacting, and its line still says so.
    assert still_running["live"] == [record_ids["live"]]
    assert after["live"].meta == _RUNNING

    # Running it again changes nothing.
    asyncio.run(migrate())
    again, _ = asyncio.run(read())
    assert {k: (r.content, r.meta, r.updated_at) for k, r in again.items()} == {
        k: (r.content, r.meta, r.updated_at) for k, r in after.items()
    }
