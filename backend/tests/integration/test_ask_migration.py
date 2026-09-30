"""e5a1c7d3b284 on rows that already exist: the answer becomes a versioned log.

A green `alembic upgrade head` proves nothing here. A fresh database has no ask
blocks, so `upgrade()` transforms zero rows and every clause in it could be
wrong and still pass. These tests put the PRE-migration shape into a real table
and then run the migration's own SQL, which is the only way to see what it does
to history.

The pre-migration rows are seeded as **literals** — never produced through
today's ask/answer routes. Once this migration's siblings land, those routes
write the new shape and could not produce the old one even if a test asked them
to; keeping an old-shape writer alive to feed this file would be exactly the
compat branch the switchover refuses to leave behind. Each literal carries a
comment naming the writer it was copied from, so it can be checked against
`git show` rather than against what the migration is now expected to produce.

The SQL under test is imported from the migration module rather than copied
here, so the statement that runs is the statement that ships.
"""

import importlib.util
import json
import uuid
from pathlib import Path

import anyio
from sqlalchemy import text

# `alembic/versions/` is not a package (no __init__.py), so the module is loaded
# from its file path — the same file alembic itself executes.
_MIGRATION_FILE = (
    Path(__file__).resolve().parent.parent.parent
    / "alembic"
    / "versions"
    / "e5a1c7d3b284_an_answer_is_a_versioned_log.py"
)

#: `ask_options` wrote exactly two keys. Copied from `topics_messages.py` before
#: this change: `meta={"options": options, "asked": asked}`, with `options` a
#: plain list of strings. Three entries so a reordering in the rebuild shows up.
PRE_ASK_META = {
    "options": ["cursor", "pageStart", "offset"],
    "asked": "user-1",
}

#: `answer_options` added two keys on top. Copied from `topics.py` before this
#: change: `meta["answered"] = option` and `meta["answered_by"] = author`.
#: Nothing recorded *when*, which is why the migrated entry must carry `at: null`
#: rather than a timestamp read off the migration clock.
PRE_ANSWER_META = {
    **PRE_ASK_META,
    "answered": "pageStart",
    "answered_by": "user-1",
}

#: A question nobody was pointed at: platform turns ask as nobody, and `asked`
#: has to survive as None instead of becoming "" or disappearing.
PRE_PLATFORM_META = {**PRE_ASK_META, "asked": None}

#: What the new writer produces, as the INPUT to `downgrade()`. Seeded for the
#: same reason the rest is: the downgrade runs against post-migration data, and
#: a test that built it through the answer route would be asserting on the route
#: as much as on the migration.
POST_META_TWO_VERSIONS = {
    "options": [{"text": "cursor"}, {"text": "pageStart"}, {"text": "offset"}],
    "asked": "user-1",
    "allow_other": True,
    "reject_option": True,
    "answer_log": [
        {
            "v": 1,
            "kind": "option",
            "option": "pageStart",
            "note": None,
            "by": "user-1",
            "at": None,
            "client_op_id": "migrated",
        },
        {
            "v": 2,
            "kind": "option",
            "option": "cursor",
            "note": None,
            "by": "user-1",
            "at": "2026-09-30T00:00:00Z",
            "client_op_id": "op-2",
        },
    ],
}


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
    """Write meta straight through — this is the seeding step, not a shortcut."""
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


def _seed_question(client, tid: str, meta: dict) -> str:
    """One timeline message whose ``meta`` is the seeded pre-migration shape.

    The block row comes from the plain message route because its columns are
    not what this migration touches. Only ``meta`` is under test, so only
    ``meta`` is seeded. That route publishes as a room agent (it accepts no
    human author), which is fine: the author column is not under test either.
    """
    from app.core.sandbox_auth import mint_scoped_token

    project_id = client.get(f"/topics/{tid}").json()["data"]["project_id"]
    r = client.post(
        f"/topics/{tid}/messages",
        json={"content": "占位：这是一道题", "request_id": str(uuid.uuid4())},
        headers={
            "X-Cheese-Token": mint_scoped_token(
                project_id=str(project_id), topic_id=tid
            )
        },
    )
    assert r.status_code == 200, r.text
    block_id = r.json()["data"]["id"]
    anyio.run(_set_meta, block_id, meta)
    return block_id


def _meta(client, tid: str, block_id: str) -> dict:
    blocks = client.get(f"/topics/{tid}/blocks").json()["data"]["data"]
    for block in blocks:
        if block["id"] == block_id:
            return block["meta"] or {}
    raise AssertionError(f"block {block_id} not on the timeline")


def test_upgrade_keeps_option_order_and_never_invents_a_timestamp(client):
    tid = _topic(client)
    answered = _seed_question(client, tid, PRE_ANSWER_META)
    platform = _seed_question(client, tid, PRE_PLATFORM_META)

    anyio.run(_apply, "upgrade")

    meta = _meta(client, tid, answered)
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
    assert log[0]["note"] is None
    assert log[0]["client_op_id"] == "migrated"
    assert meta["asked"] == "user-1"
    # Nothing the question did not have is added to it.
    for key in ("allow_other", "reject_option", "ask_group", "group_settle"):
        assert key not in meta

    platform_meta = _meta(client, tid, platform)
    assert platform_meta["asked"] is None
    texts = [o["text"] for o in platform_meta["options"]]
    assert texts == ["cursor", "pageStart", "offset"]
    assert "answer_log" not in platform_meta


def test_downgrade_restores_the_last_answer_only(client):
    tid = _topic(client)
    two_versions = _seed_question(client, tid, POST_META_TWO_VERSIONS)

    anyio.run(_apply, "downgrade")

    meta = _meta(client, tid, two_versions)
    assert meta["options"] == ["cursor", "pageStart", "offset"]
    assert "answer_log" not in meta
    # The version in force comes back; the replaced one does not. The two
    # single-valued keys cannot hold a correction — which is what the downgrade
    # has to admit rather than paper over.
    assert meta["answered"] == "cursor"
    assert meta["answered_by"] == "user-1"


def test_upgrade_leaves_what_it_does_not_understand_alone(client):
    """Only the shape the ask route ever produced is touched.

    `options` as objects, or as something that is not a list at all, was never
    written by production. Guessing at what it means would be a second
    corruption on top of the one being fixed.
    """
    tid = _topic(client)
    already_objects = _seed_question(
        client,
        tid,
        {"options": [{"text": "cursor"}], "asked": "user-1", "answered": "cursor"},
    )
    not_a_list = _seed_question(client, tid, {"options": "cursor", "asked": "user-1"})

    anyio.run(_apply, "upgrade")

    first = _meta(client, tid, already_objects)
    assert first["options"] == [{"text": "cursor"}]
    assert first["answered"] == "cursor"
    assert "answer_log" not in first

    second = _meta(client, tid, not_a_list)
    assert second["options"] == "cursor"
    assert "answer_log" not in second


def test_the_new_reader_reads_migrated_history(client):
    """历史答案不失，而且新读端读得懂它。

    迁移之后，`_awaiting_an_answer` 认的是 `answer_log`：答过的那道题不再挂在
    待办上，没答过的照旧挂着。这一条同时钉住「迁移写出来的形状」和「新读端读的
    形状」是同一个 —— 两边各自绿而形状不合，是这次切换最容易出的错。
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.domain.block.repositories import BlockRepository

    answered_room = _topic(client)
    answered = _seed_question(client, answered_room, PRE_ANSWER_META)
    anyio.run(_apply, "upgrade")
    migrated = _meta(client, answered_room, answered)
    assert migrated["answer_log"][0]["option"] == "pageStart"

    unanswered_room = _topic(client)
    _seed_question(client, unanswered_room, PRE_ASK_META)
    anyio.run(_apply, "upgrade")

    async def _read():
        engine = _engine()
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                return await BlockRepository(session).rooms_awaiting_an_answer(
                    [uuid.UUID(answered_room), uuid.UUID(unanswered_room)]
                )
        finally:
            await engine.dispose()

    waiting = anyio.run(_read)
    assert uuid.UUID(answered_room) not in waiting, "答过的题不该再挂待办"
    assert waiting[uuid.UUID(unanswered_room)] == "user-1"
