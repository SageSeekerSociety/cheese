"""表情：Slack 语义的增删，与按块聚合起来的读数。

从 `repositories.py` 拆出来的一块（理由写在那边：文件体积，不是分层）。`toggle`、
`add_if_absent` 与它一次写多块的批量形式是写，`reactions_for_blocks` 是读，两者共用
同一张表和同一条语义 —— 同一个人同一个表情只算一次 —— 所以留在一个文件里。

`BlockRepository` 继承这里的 `Reactions`。这里有一个**按名字打的补丁**：
`tests/integration/test_block_paging.py` 把 `BlockRepository.reactions_for_blocks`
换成探针。方法从基类继承不影响它：补丁打在类对象上，属性查找走 MRO。
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Uuid,
    any_,
    bindparam,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.request_facts import recall, remember
from app.domain.block.models import (
    BlockReaction,
)

_REACTION_TABLES = frozenset({"block_reactions"})


class Reactions:
    """表情的增删与聚合；Slack 语义：同一个人同一个表情只算一次。

    没有 `__init__`：session 由 `BlockRepository.__init__` 装上来；这些方法本来
    就只碰 `self._session` 和类属性，放在基类里与放在原来的类里是同一件事。
    """

    _session: AsyncSession

    # ---- Emoji reactions (Slack semantics) ----

    async def _get_reaction(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> BlockReaction | None:
        stmt = select(BlockReaction).where(
            BlockReaction.block_id == block_id,
            BlockReaction.emoji == emoji,
            BlockReaction.author == author,
        )
        return (await self._session.scalars(stmt)).first()

    async def toggle_reaction(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> bool:
        """Slack-style toggle: add the (emoji, author) reaction, or remove it if
        it already exists. Returns True when added, False when removed."""
        stmt = (
            select(BlockReaction)
            .where(BlockReaction.block_id == block_id)
            .order_by(BlockReaction.created_at, BlockReaction.id)
        )
        rows = list((await self._session.scalars(stmt)).all())
        existing = next(
            (r for r in rows if r.emoji == emoji and r.author == author), None
        )
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
            if isinstance(self._session, AsyncSession):
                remember(
                    self._session,
                    ("block_reaction_rows", block_id),
                    tuple(r for r in rows if r is not existing),
                    tables=_REACTION_TABLES,
                )
            return False
        added = BlockReaction(block_id=block_id, emoji=emoji, author=author)
        self._session.add(added)
        await self._session.flush()
        if isinstance(self._session, AsyncSession):
            remember(
                self._session,
                ("block_reaction_rows", block_id),
                tuple([*rows, added]),
                tables=_REACTION_TABLES,
            )
        return True

    async def add_reaction_if_absent(
        self, block_id: uuid.UUID, emoji: str, author: str
    ) -> bool:
        """Idempotent add (never removes) — for platform receipts like 芝士's 👀
        on the message that summoned it. Returns True when a row was created."""
        if await self._get_reaction(block_id, emoji, author) is not None:
            return False
        self._session.add(BlockReaction(block_id=block_id, emoji=emoji, author=author))
        await self._session.flush()
        return True

    async def add_reactions_if_absent(
        self, block_ids: list[uuid.UUID], emoji: str, author: str
    ) -> list[uuid.UUID]:
        """The batch form of `add_reaction_if_absent`: one statement for many
        blocks, same rule — an existing (block, emoji, author) is left alone, a
        missing one gets a row. Returns the blocks a row was created for.

        A delivery's read receipt covers every block it carried, and those rows
        land in its settlement transaction; writing them one block at a time
        put N round trips in that transaction. ``ON CONFLICT DO NOTHING``
        leaves the existing rows untouched instead of selecting them first."""
        ids = list(dict.fromkeys(block_ids))
        if not ids:
            return []
        stamp = datetime.now(UTC)
        created = await self._session.scalars(
            pg_insert(BlockReaction)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "block_id": block_id,
                        "emoji": emoji,
                        "author": author,
                        "created_at": stamp,
                    }
                    for block_id in ids
                ]
            )
            .on_conflict_do_nothing(constraint="uq_block_reaction")
            .returning(BlockReaction.block_id)
        )
        return list(created.all())

    async def reactions_for_blocks(
        self, block_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[dict]]:
        """Aggregated reactions for many blocks in ONE query (no N+1):
        block_id → [{"emoji", "count", "authors"}], groups ordered by the emoji's
        first appearance on the block, authors by reaction time (Slack)."""
        if not block_ids:
            return {}
        if len(block_ids) == 1 and isinstance(self._session, AsyncSession):
            if self._session.autoflush:
                await self._session.flush()
            found, cached = recall(self._session, ("block_reaction_rows", block_ids[0]))
            if found:
                rows = list(cached)
            else:
                among = any_(bindparam(None, list(block_ids), type_=ARRAY(Uuid)))
                stmt = (
                    select(BlockReaction)
                    .where(BlockReaction.block_id == among)
                    .order_by(BlockReaction.created_at, BlockReaction.id)
                )
                rows = list((await self._session.scalars(stmt)).all())
                remember(
                    self._session,
                    ("block_reaction_rows", block_ids[0]),
                    tuple(rows),
                    tables=_REACTION_TABLES,
                )
        else:
            # One array parameter, not an IN list: the whole timeline of a busy room
            # is tens of thousands of ids, and asyncpg refuses more than 32767 bound
            # values in one statement.
            among = any_(bindparam(None, list(block_ids), type_=ARRAY(Uuid)))
            stmt = (
                select(BlockReaction)
                .where(BlockReaction.block_id == among)
                .order_by(BlockReaction.created_at, BlockReaction.id)
            )
            rows = (await self._session.scalars(stmt)).all()
        grouped: dict[uuid.UUID, dict[str, dict]] = {}
        for r in rows:
            per_block = grouped.setdefault(r.block_id, {})
            agg = per_block.setdefault(
                r.emoji, {"emoji": r.emoji, "count": 0, "authors": []}
            )
            agg["count"] += 1
            agg["authors"].append(r.author)
        return {bid: list(per.values()) for bid, per in grouped.items()}

    async def reactions_for_block(self, block_id: uuid.UUID) -> list[dict]:
        """Aggregated reactions of one block (same shape as the batch form)."""
        agg = await self.reactions_for_blocks([block_id])
        return agg.get(block_id, [])
