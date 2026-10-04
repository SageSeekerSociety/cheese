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


async def _set_row(
    block_id: str, *, meta: dict | None = None, author: str | None = None
) -> None:
    """Write the seeded columns straight through — not a shortcut around a route.

    Both columns are seeded: ``meta`` is what the migration rewrites, and
    ``author`` is what decides whether the row is in its scope at all.
    """
    engine = _engine()
    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            if meta is not None:
                await session.execute(
                    text("UPDATE blocks SET meta = CAST(:meta AS json) WHERE id = :id"),
                    {"id": uuid.UUID(block_id), "meta": json.dumps(meta)},
                )
            if author is not None:
                await session.execute(
                    text("UPDATE blocks SET author = :author WHERE id = :id"),
                    {"id": uuid.UUID(block_id), "author": author},
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


def _seed_question(client, tid: str, meta: dict, *, author: str | None = None) -> str:
    """One timeline message carrying the seeded pre-migration shape.

    The block row comes from the plain message route because its columns are
    not what this migration rewrites. ``meta`` is seeded because that is what
    the migration transforms; ``author`` is seeded too because it is what
    decides whether the row is in scope — who signed the question. That route
    publishes as a room agent (it accepts no human author), which is why the
    signature is written straight through on top of it.
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
    anyio.run(lambda: _set_row(block_id, meta=meta, author=author))
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


def test_the_scope_is_who_signed_the_question_including_a_retired_seat(client):
    """升级和降级认的是「谁签的这道题」，不是今天的名册。

    退席的那条 `agent_instances` 行还在：退役只把 `is_active` 置 false，行不删，
    因为记忆池还挂在它上面，房间也还指向它。所以它署过名的历史照样是 agent 的
    历史，两个方向都在范围内。人的题从头到尾没被碰过——人自己的答题路径今天写的
    还是那两个键，把它搬进 `answer_log` 反而是替它编了一段它没有的历史。
    """
    tid = _topic(client)
    project_id = client.get(f"/topics/{tid}").json()["data"]["project_id"]

    made = client.post(
        f"/projects/{project_id}/agents",
        json={"handle": "planner", "display_name": "规划师"},
    ).json()["data"]
    seat = made["seat_handle"]
    assert (
        client.delete(f"/projects/{project_id}/agents/{made['id']}").status_code == 200
    )
    roster = client.get(f"/projects/{project_id}/agents").json()["data"]["data"]
    retired = next(row for row in roster if row["id"] == made["id"])
    # 退了，行还在，署名用的那个 handle 也没变——这就是按署名划范围要覆盖它的地方。
    assert retired["is_active"] is False
    assert retired["seat_handle"] == seat

    by_retired_seat = _seed_question(client, tid, PRE_ANSWER_META, author=seat)
    # 芝士这个名字是实例行出现之前签下的历史，范围里单列一条。
    by_default_agent = _seed_question(client, tid, PRE_ASK_META, author="cheese")
    by_a_person = _seed_question(client, tid, PRE_ANSWER_META, author="user-1")

    anyio.run(_apply, "upgrade")

    moved = _meta(client, tid, by_retired_seat)
    assert [o["text"] for o in moved["options"]] == ["cursor", "pageStart", "offset"]
    assert "answered" not in moved and "answered_by" not in moved
    assert moved["answer_log"] == [
        {
            "v": 1,
            "kind": "option",
            "option": "pageStart",
            "note": None,
            "by": "user-1",
            "at": None,
            "client_op_id": "migrated",
        }
    ]

    legacy = _meta(client, tid, by_default_agent)
    assert [o["text"] for o in legacy["options"]] == ["cursor", "pageStart", "offset"]
    assert "answer_log" not in legacy

    untouched = _meta(client, tid, by_a_person)
    assert untouched == PRE_ANSWER_META, "人的题不该被搬进 answer_log"

    anyio.run(_apply, "downgrade")

    back = _meta(client, tid, by_retired_seat)
    assert back["options"] == ["cursor", "pageStart", "offset"]
    assert back["answered"] == "pageStart"
    assert back["answered_by"] == "user-1"
    assert "answer_log" not in back

    legacy_back = _meta(client, tid, by_default_agent)
    assert legacy_back["options"] == ["cursor", "pageStart", "offset"]
    assert "answer_log" not in legacy_back
    assert "answered" not in legacy_back

    assert _meta(client, tid, by_a_person) == PRE_ANSWER_META
