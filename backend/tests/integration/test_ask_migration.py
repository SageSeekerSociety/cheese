"""e5a1c7d3b284 on rows that already exist: the answer becomes a versioned log.

The suite's schema is always built by `alembic upgrade head`, so a green run
only proves the migration is executable — a fresh database has no ask blocks,
so `upgrade()` is a no-op and would pass even if every clause were wrong.
These tests put the OLD shape in a real table first and then run the
migration's own SQL, which is the only way to see what it does to history.

The rows are made by the ask route itself, so the shape under test is the shape
production actually wrote. The SQL is imported from the migration module rather
than copied here, so the statement under test is the statement that ships.
"""

import importlib.util
import json
import uuid
from pathlib import Path

from sqlalchemy import text

# `alembic/versions/` is not a package (no __init__.py), so the module is loaded
# from its file path — the same file alembic itself executes.
_MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "alembic"
    / "versions"
    / "e5a1c7d3b284_an_answer_is_a_versioned_log.py"
)


class _Capture:
    """Stand-in for alembic's `op`: collect the SQL instead of needing a context."""

    def __init__(self):
        self.statements: list[str] = []

    def execute(self, sql):  # noqa: ANN001 - alembic passes text()/raw strings
        self.statements.append(str(sql))


def _statements(fn_name: str) -> list[str]:
    spec = importlib.util.spec_from_file_location(
        "e5a1c7d3b284_an_answer_is_a_versioned_log", _MIGRATION_FILE
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    captured = _Capture()
    import alembic.op as op_module

    original = op_module.execute
    op_module.execute = captured.execute
    try:
        getattr(module, fn_name)()
    finally:
        op_module.execute = original
    assert captured.statements, f"{fn_name}() issued no SQL"
    return captured.statements


def _engine():
    """The database the `client` fixture writes to.

    The harness keeps two: `settings.database_url` (the integration DB the app
    engines bind) and `TEST_DATABASE_URL` (the client/python_client DB). Rows
    made through `client` land in the second one, so the migration has to run
    there too or it transforms an empty table and every assertion reads stale
    rows.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    from tests.conftest import TEST_DATABASE_URL

    return create_async_engine(TEST_DATABASE_URL)


async def _apply(fn_name: str) -> None:
    engine = _engine()
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        for statement in _statements(fn_name):
            async with factory() as session:
                await session.execute(text(statement))
                await session.commit()
    finally:
        await engine.dispose()


async def _set_meta(block_id: str, meta: dict) -> None:
    """Write meta straight through, for shapes the route cannot produce."""
    engine = _engine()
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            await session.execute(
                text("UPDATE blocks SET meta = CAST(:meta AS json) WHERE id = :id"),
                {"id": uuid.UUID(block_id), "meta": json.dumps(meta)},
            )
            await session.commit()
    finally:
        await engine.dispose()


def _topic(client) -> str:
    from tests.integration.conftest import post_project

    p = post_project(client, json={"name": "P"}).json()["data"]
    t = client.post(
        "/topics",
        json={"project_id": p["id"], "title": "T", "created_by": "user-1"},
    ).json()["data"]
    return t["id"]


def _ask(client, tid: str, options: list[str]) -> dict:
    r = client.post(
        f"/topics/{tid}/ask",
        json={"question": "分页方案选哪个？", "options": options},
    )
    assert r.status_code == 200
    return r.json()["data"]


def _answer(client, block_id: str, option: str) -> None:
    r = client.post(
        f"/topics/blocks/{block_id}/answer",
        json={"option": option, "author": "user-1"},
    )
    assert r.status_code == 200


def _meta(client, tid: str, block_id: str) -> dict:
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    for block in blocks:
        if block["id"] == block_id:
            return block["meta"] or {}
    raise AssertionError(f"block {block_id} not on the timeline")


def test_upgrade_keeps_option_order_and_never_invents_a_timestamp(client, anyio_backend):
    tid = _topic(client)

    answered = _ask(client, tid, ["cursor", "pageStart", "offset"])
    _answer(client, answered["id"], "pageStart")
    unanswered = _ask(client, tid, ["是", "否"])
    platform = _ask(client, tid, ["A", "B"])
    _answer(client, platform["id"], "A")

    before = _meta(client, tid, answered["id"])
    assert before["options"] == ["cursor", "pageStart", "offset"]
    assert before["answered"] == "pageStart"

    # Platform turns ask as nobody (`asked is None`), which the route cannot
    # produce on demand; the key must survive the migration unchanged.
    import anyio

    async def _null_asked():
        await _set_meta(
            platform["id"], {**_meta(client, tid, platform["id"]), "asked": None}
        )

    anyio.run(_null_asked)

    anyio.run(_apply, "upgrade")

    meta = _meta(client, tid, answered["id"])
    # Order is the only thing that makes an option list mean anything.
    assert [o["text"] for o in meta["options"]] == ["cursor", "pageStart", "offset"]
    assert "answered" not in meta and "answered_by" not in meta

    log = meta["answer_log"]
    assert len(log) == 1
    assert log[0]["v"] == 1
    assert log[0]["kind"] == "option"
    assert log[0]["option"] == "pageStart"
    assert log[0]["by"] == "user-1"
    # The block never recorded when it was answered, so the entry says so.
    assert log[0]["at"] is None
    assert log[0]["client_op_id"] == "migrated"
    assert meta["asked"] == before["asked"]
    # Nothing the question did not have is added to it.
    for key in ("allow_other", "reject_option", "ask_group", "group_settle"):
        assert key not in meta

    empty = _meta(client, tid, unanswered["id"])
    assert [o["text"] for o in empty["options"]] == ["是", "否"]
    assert "answer_log" not in empty

    platform_meta = _meta(client, tid, platform["id"])
    assert platform_meta["asked"] is None
    assert len(platform_meta["answer_log"]) == 1
    assert platform_meta["answer_log"][0]["at"] is None


def test_downgrade_restores_the_last_answer_only(client, anyio_backend):
    tid = _topic(client)
    answered = _ask(client, tid, ["cursor", "pageStart", "offset"])
    _answer(client, answered["id"], "pageStart")
    unanswered = _ask(client, tid, ["是", "否"])

    import anyio

    anyio.run(_apply, "upgrade")

    meta = _meta(client, tid, answered["id"])
    # A corrected version. The two single-valued keys cannot hold it, which is
    # what downgrade has to admit rather than paper over.
    meta["answer_log"].append(
        {
            "v": 2,
            "kind": "option",
            "option": "cursor",
            "note": None,
            "by": "user-1",
            "at": "2026-09-30T00:00:00Z",
            "client_op_id": "op-2",
        }
    )
    anyio.run(_set_meta, answered["id"], meta)

    anyio.run(_apply, "downgrade")

    meta = _meta(client, tid, answered["id"])
    assert meta["options"] == ["cursor", "pageStart", "offset"]
    assert "answer_log" not in meta
    # The version in force comes back; the replaced one does not.
    assert meta["answered"] == "cursor"
    assert meta["answered_by"] == "user-1"

    empty = _meta(client, tid, unanswered["id"])
    assert empty["options"] == ["是", "否"]
    assert "answered" not in empty
