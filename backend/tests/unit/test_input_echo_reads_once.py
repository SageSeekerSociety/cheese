"""A delivery's read receipt is one write for the whole batch, not one per block.

`apply_input_echo` marks every block a delivery carried as seen by its receiver
— one 👀 each. It used to call `add_reaction_if_absent` per block: a SELECT and
an INSERT … ON CONFLICT for each of them, serialised inside the delivery's own
settlement transaction. `add_reactions_if_absent` writes the same rows in one
statement; the echo path must call it exactly once whatever N is, and a block
that had no 👀 yet must still get one.

The last three tests pin what the batch entry actually sends: a single
multi-row INSERT … ON CONFLICT DO NOTHING, with no duplicates and no statement
at all when there is nothing to mark.
"""

import asyncio
import uuid
from typing import cast

import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block import input_effects
from app.domain.block.repositories import BlockRepository


class _RecordingRepository:
    """What `BlockRepository` looks like to the echo path: the same "a row that
    is already there is not written twice" rule, plus a note of which entry the
    caller used. `add_reaction_if_absent` is kept as the way a regression shows
    up — one call per block instead of one for the batch."""

    def __init__(self, already: set[uuid.UUID] | None = None) -> None:
        self.rows: set[uuid.UUID] = set(already or ())
        self.batch_calls: list[tuple[list[uuid.UUID], str, str]] = []
        self.single_calls: list[tuple[uuid.UUID, str, str]] = []

    async def mark_consumed(self, block_ids, work_id) -> None:
        raise AssertionError("work_id is None here; nothing should be consumed")

    async def add_reaction_if_absent(self, block_id, emoji, author) -> bool:
        self.single_calls.append((block_id, emoji, author))
        fresh = block_id not in self.rows
        self.rows.add(block_id)
        return fresh

    async def add_reactions_if_absent(
        self, block_ids, emoji, author
    ) -> list[uuid.UUID]:
        self.batch_calls.append((list(block_ids), emoji, author))
        fresh = [block_id for block_id in block_ids if block_id not in self.rows]
        self.rows.update(block_ids)
        return fresh


def _install(monkeypatch, already: set[uuid.UUID] | None = None):
    """Point `input_effects` at a recording repository instead of the database."""
    repo = _RecordingRepository(already)
    monkeypatch.setattr(input_effects, "BlockRepository", lambda session: repo)
    return repo


def _run_echo(seen_ids, *, seen_by: str = "cheese") -> None:
    async def echo() -> None:
        await input_effects.apply_input_echo(
            cast(AsyncSession, None),
            consumed_ids=[],
            work_id=None,
            seen_ids=list(seen_ids),
            seen_by=seen_by,
        )

    asyncio.run(echo())


@pytest.mark.parametrize("size", [3, 12])
def test_the_receipt_is_written_once_however_many_blocks(monkeypatch, size):
    repo = _install(monkeypatch)
    seen = [uuid.uuid4() for _ in range(size)]

    _run_echo(seen)

    assert len(repo.batch_calls) == 1, (
        f"{size} 个 seen_ids 走了 {len(repo.batch_calls)} 次批量入口"
        " —— 整批已读应当是一条语句"
    )
    assert repo.single_calls == [], "回执又回到逐块写了"
    covered, emoji, author = repo.batch_calls[0]
    assert covered == seen
    assert (emoji, author) == ("👀", "cheese")


def test_blocks_without_the_receipt_still_get_one(monkeypatch):
    already = [uuid.uuid4() for _ in range(2)]
    missing = [uuid.uuid4() for _ in range(3)]
    repo = _install(monkeypatch, already=set(already))

    _run_echo([*already, *missing])

    # 假仓储的 rows 是它自己按唯一约束模拟出来的账，证据是「整批都交过去了」：
    # 真实的「已有的不重复、缺的补上」由下面钉语句的那组用例证明。
    assert repo.rows == set(already + missing), "缺的那几条没有补上"
    assert len(repo.batch_calls) == 1
    assert repo.batch_calls[0][0] == [*already, *missing]


def test_nothing_to_mark_sends_no_statement(monkeypatch):
    repo = _install(monkeypatch)

    _run_echo([])

    assert repo.batch_calls == []
    assert repo.single_calls == []


class _ScalarRows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return list(self._rows)


class _CapturingSession:
    """Only what `add_reactions_if_absent` asks of a session: `scalars`."""

    def __init__(self, created) -> None:
        self.created = created
        self.statements = []

    async def scalars(self, statement):
        self.statements.append(statement)
        return _ScalarRows(self.created)


def _compiled(session: _CapturingSession):
    (statement,) = session.statements
    return statement.compile(dialect=postgresql.dialect())


def _written_blocks(compiled) -> list[uuid.UUID]:
    """每行 VALUES 里绑的那个 block —— 行数就是语句要写的行数。"""
    return [
        compiled.params[key]
        for key in sorted(compiled.params)
        if key.startswith("block_id_m")
    ]


def test_the_batch_entry_sends_one_multi_row_insert():
    ids = [uuid.uuid4() for _ in range(4)]
    created = ids[1:]
    session = _CapturingSession(created)
    repo = BlockRepository(cast(AsyncSession, session))

    fresh = asyncio.run(repo.add_reactions_if_absent(ids, "👀", "cheese"))

    assert fresh == created, "应返回真正新建的那些块"
    compiled = _compiled(session)
    sql = str(compiled)
    assert sql.count("INSERT INTO block_reactions") == 1
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
    assert "uq_block_reaction" in sql, "冲突目标是那条唯一约束"
    # 每一行写它自己那个块。都写第一个块的话，其余行撞唯一约束后被
    # DO NOTHING 丢掉，语句照样合法 —— 所以这里钉住绑定进去的值。
    assert _written_blocks(compiled) == ids, "每块的 👀 应当各写一行"


def test_a_block_listed_twice_is_written_once():
    block = uuid.uuid4()
    session = _CapturingSession([block])
    repo = BlockRepository(cast(AsyncSession, session))

    asyncio.run(repo.add_reactions_if_absent([block, block], "👀", "cheese"))

    assert _written_blocks(_compiled(session)) == [block]


def test_an_empty_batch_sends_nothing():
    session = _CapturingSession([])
    repo = BlockRepository(cast(AsyncSession, session))

    assert asyncio.run(repo.add_reactions_if_absent([], "👀", "cheese")) == []
    assert session.statements == []
