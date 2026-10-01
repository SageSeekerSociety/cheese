"""A decision recorded before the primitive was removed reads as a message.

Agents used to record decisions as their own kind of block. That kind is gone:
a decision is something said in the conversation. Rows written before then
must still show up in the room, as their author said them, and still be found
by search. This plants one such row, runs the migration that converts them
against the real database, and reads it back through the routes a person uses.
"""

import asyncio
import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_DATABASE_URL
from tests.integration.conftest import post_project

_MIGRATION = next(
    (Path(__file__).resolve().parents[2] / "alembic" / "versions").glob(
        "*_a_recorded_decision_is_a_message.py"
    )
)


def _room(client) -> tuple[str, str]:
    p = post_project(client, json={"name": "P", "owner_handle": "alice"})
    project_id = p.json()["data"]["id"]
    r = client.post("/topics", json={"project_id": project_id, "title": "排期"})
    return project_id, r.json()["data"]["id"]


def _run_migration(sync_conn) -> None:
    spec = importlib.util.spec_from_file_location("decision_migration", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


async def _an_old_decision_then_migrate(block_id: str) -> None:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE blocks SET kind = 'decision' WHERE id = :id"),
                {"id": block_id},
            )
        async with engine.begin() as conn:
            await conn.run_sync(_run_migration)
    finally:
        await engine.dispose()


def test_an_old_decision_shows_in_the_room_and_in_search_as_a_message(client):
    project_id, room = _room(client)
    # Any block the room's agent writes will do as the row to age; the migration
    # only looks at the kind.
    written = client.post(
        f"/topics/{room}/weekly", json={"body": "上线日期定在 10 月 8 日"}
    )
    assert written.status_code == 200, written.text
    block_id = written.json()["data"]["id"]
    author = written.json()["data"]["author"]

    asyncio.run(_an_old_decision_then_migrate(block_id))

    timeline = client.get(f"/topics/{room}/blocks")
    assert timeline.status_code == 200, timeline.text
    (row,) = [b for b in timeline.json()["data"]["data"] if b["id"] == block_id]
    assert row["kind"] == "message"
    assert row["author"] == author
    assert row["content"] == "上线日期定在 10 月 8 日"

    found = client.get(
        f"/projects/{project_id}/context/search",
        params={"q": "上线日期", "topic": room, "only": "message"},
    )
    assert found.status_code == 200, found.text
    assert [h["id"] for h in found.json()["data"]["hits"]["records"]] == [block_id]
