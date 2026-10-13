"""a7c3e9d1f520 on questions that already exist: `asked` becomes a list.

The rows are seeded in the shape the writer had before (`block.questions.
post_questions`: ``"asked": handle`` or ``None``), then the migration's own SQL
runs against them, loaded from its file so the statement under test is the one
that ships.
"""

import importlib.util
import json
import uuid
from pathlib import Path

from sqlalchemy import text

from tests.ask_fixtures import question_row
from tests.conftest import seed_user
from tests.integration.conftest import in_thread, post_project

_MIGRATION_FILE = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "a7c3e9d1f520_a_question_asks_a_list_of_people.py"
)


def _statements(fn_name: str) -> list[str]:
    spec = importlib.util.spec_from_file_location("a7c3e9d1f520", _MIGRATION_FILE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import alembic.op as op_module

    seen: list[str] = []
    original = op_module.execute
    op_module.execute = lambda sql: seen.append(str(sql))
    try:
        getattr(module, fn_name)()
    finally:
        op_module.execute = original
    return seen


def _run(client, sql: str, params: dict | None = None):
    async def go():
        async with client.test_factory() as session:
            result = await session.execute(text(sql), params or {})
            await session.commit()
            return result

    return client.portal.call(go)


def _meta(client, block_id: str) -> dict:
    async def go():
        async with client.test_factory() as session:
            raw = await session.scalar(
                text("SELECT meta::text FROM blocks WHERE id = :id"),
                {"id": uuid.UUID(block_id)},
            )
            return json.loads(raw)

    return client.portal.call(go)


def _old_question(client, room, asked) -> str:
    block = question_row(client, room)
    meta = {"options": [{"text": "a"}, {"text": "b"}], "asked": asked, "answer_log": []}
    _run(
        client,
        "UPDATE blocks SET meta = CAST(:meta AS json) WHERE id = :id",
        {"id": uuid.UUID(block["id"]), "meta": json.dumps(meta)},
    )
    return block["id"]


def test_a_handle_becomes_a_list_of_one_and_nobody_the_empty_list(client):
    seed_user(client, "alice")
    data = post_project(client, {"name": "M"}, owner="alice").json()["data"]
    room = in_thread(client, data["root_topic_id"], "alice")
    one = _old_question(client, room, "alice")
    nobody = _old_question(client, room, None)

    for statement in _statements("upgrade"):
        _run(client, statement)
    # A second run finds nothing left to convert.
    for statement in _statements("upgrade"):
        _run(client, statement)

    assert _meta(client, one)["asked"] == ["alice"]
    assert _meta(client, nobody)["asked"] == []
    assert _meta(client, one)["options"] == [{"text": "a"}, {"text": "b"}]
