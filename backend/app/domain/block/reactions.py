"""表情：Slack 语义的增删，与按块聚合起来的读数。

从 `repositories.py` 拆出来的一块（理由写在那边：文件体积，不是分层）。`toggle` 与
`add_if_absent` 是写，`reactions_for_blocks` 是读，两者共用同一张表和同一条语义 ——
同一个人同一个表情只算一次 —— 所以留在一个文件里。

`BlockRepository` 继承这里的 `Reactions`。这里有一个**按名字打的补丁**：
`tests/integration/test_block_paging.py` 把 `BlockRepository.reactions_for_blocks`
换成探针。方法从基类继承不影响它：补丁打在类对象上，属性查找走 MRO。
"""

import uuid

from sqlalchemy import (
    Uuid,
    any_,
    bindparam,
    select,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.models import (
    BlockReaction,
)


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
        existing = await self._get_reaction(block_id, emoji, author)
        if existing is not None:
            await self._session.delete(existing)
            await self._session.flush()
            return False
        self._session.add(BlockReaction(block_id=block_id, emoji=emoji, author=author))
        await self._session.flush()
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

    async def reactions_for_blocks(
        self, block_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[dict]]:
        """Aggregated reactions for many blocks in ONE query (no N+1):
        block_id → [{"emoji", "count", "authors"}], groups ordered by the emoji's
        first appearance on the block, authors by reaction time (Slack)."""
        if not block_ids:
            return {}
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
