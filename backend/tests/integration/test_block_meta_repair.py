"""A block whose meta was left as a JSON array comes back as the object it meant.

Migration c9f4a2b7e130 stamped ``in_room: false`` on every non-system event with
``COALESCE(meta::jsonb, '{}') || '{"in_room": false}'``. COALESCE only replaces
SQL NULL; a row holding the JSON literal ``null`` kept it, and jsonb's ``||``
with a non-object on the left concatenates into an array —
``[null, {"in_room": false}]``. One such row fails ``BlockOut``, and that one
failure answered ``GET /topics/{id}/blocks`` with a 500 for the whole topic.

Migration 89fb9b9a11e0 repairs the rows. This test plants one, runs that
migration's ``upgrade()`` against the real database, and reads the timeline
back through the route — the thing that was broken.
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

_REPAIR = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "89fb9b9a11e0_block_meta_is_an_object.py"
)

_CORRUPT = '[null, {"in_room": false}]'


def _topic(client) -> str:
    p = client.post("/projects", json={"name": "P"}).json()["data"]
    t = client.post("/topics", json={"project_id": p["id"], "title": "T"}).json()[
        "data"
    ]
    return t["id"]


def _say(client, tid: str, text_: str) -> str:
    r = client.post(f"/topics/{tid}/decision", json={"decision": text_})
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def _run_repair_migration(sync_conn) -> None:
    spec = importlib.util.spec_from_file_location("repair_migration", _REPAIR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(sync_conn)):
        module.upgrade()


async def _corrupt_then_repair(block_id: str) -> str:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE blocks SET meta = CAST(:m AS json) WHERE id = :id"),
                {"m": _CORRUPT, "id": block_id},
            )
            shape = await conn.scalar(
                text("SELECT json_typeof(meta) FROM blocks WHERE id = :id"),
                {"id": block_id},
            )
        assert shape == "array", "the corrupt row must be in place before the repair"
        async with engine.begin() as conn:
            await conn.run_sync(_run_repair_migration)
            return await conn.scalar(
                text("SELECT json_typeof(meta) FROM blocks WHERE id = :id"),
                {"id": block_id},
            )
    finally:
        await engine.dispose()


def test_array_meta_is_repaired_and_the_timeline_loads_again(client) -> None:
    tid = _topic(client)
    bid = _say(client, tid, "hello")

    assert asyncio.run(_corrupt_then_repair(bid)) == "object"

    r = client.get(f"/topics/{tid}/blocks")
    assert r.status_code == 200, r.text
    (block,) = [b for b in r.json()["data"]["data"] if b["id"] == bid]
    assert block["meta"] == {"in_room": False}
