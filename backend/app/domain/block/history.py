"""话题时间线上的定点小读：最近说过什么、某一条之前说过什么。

从 `repositories.py` 拆出来的一块（理由写在那边：文件体积，不是分层）。这一组都按
「时间线上的一个点或一个窗口」取，结果不超过几打块，没有游标、没有计数，也不走
`paging.py` 的 `_narrow` / `BlockPage`。

`BlockRepository` 继承这里的 `HistoryQueries`；方法签名与 SQL 逐字未动。
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    and_,
    cast,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.block.authorship import participant_blocks
from app.domain.block.models import (
    AGENT_NOTICE_META_KEY,
    CONSUMED_TURN_META_KEY,
    Block,
    BlockKind,
)
from app.domain.identity.handles import agent_handle_column


class HistoryQueries:
    """时间线上的定点小读：最近说过什么、某一条之前说过什么。

    没有 `__init__`：session 由 `BlockRepository.__init__` 装上来；这些方法本来
    就只碰 `self._session` 和类属性，放在基类里与放在原来的类里是同一件事。
    """

    _session: AsyncSession

    async def turn_history(self, conversation_id: uuid.UUID) -> list[Block]:
        """Inputs awaiting consumption and the latest AI message, their watermark.

        Inputs, not the timeline: a room's events are mostly for people to read,
        and only the ones whose author wrote a sentence for 芝士 are addressed to
        it. Those are selected by carrying that sentence, the same way a human
        input is selected by being one — not by their kind, which says who can
        see them rather than who they are for.
        """
        place = (Block.conversation_id == conversation_id,)
        latest_ai = (
            select(Block.id)
            .where(
                *place,
                agent_handle_column(Block.author),
                Block.kind == BlockKind.message,
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
            .scalar_subquery()
        )
        # The latest AI message remains the watermark for untracked legacy
        # inputs. Explicitly pending inputs can precede it and must survive.
        stmt = (
            select(Block)
            .where(
                *place,
                or_(
                    Block.id == latest_ai,
                    and_(
                        participant_blocks(),
                        Block.kind.in_((BlockKind.message, BlockKind.attachment)),
                        Block.meta[CONSUMED_TURN_META_KEY].as_string().is_(None),
                        # 没有戳的块有两种：还没被读过的（带 null 标记），和记账
                        # 存在之前写下的（什么也不带）。第二种只有靠上面那条水位
                        # 线才判得了，而水位线本身就是最后一条芝士的消息 —— 一条
                        # 芝士签名的老消息永远在它之前，取回来也只是被丢掉。房间
                        # 的历史只增不减，所以在这里挡掉，不是在 Python 里。
                        or_(
                            cast(Block.meta, JSONB).has_key(CONSUMED_TURN_META_KEY),
                            ~agent_handle_column(Block.author),
                        ),
                    ),
                    and_(
                        Block.meta[AGENT_NOTICE_META_KEY].as_string().is_not(None),
                        Block.meta[CONSUMED_TURN_META_KEY].as_string().is_(None),
                    ),
                ),
            )
            .order_by(Block.created_at, Block.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def latest_for_topic(self, conversation_id: uuid.UUID) -> Block | None:
        """The newest block in the topic's timeline, or None for an empty topic.

        Same total order and same exclusions as `list_for_topic`, so "the last
        thing in the topic" means here exactly what the reader sees at the
        bottom of the conversation — which is what makes a stall verdict built
        on it checkable by hand.

        `topic` reads the same row through a bare table
        (`topic/repositories/latest.py`), because it sits in layer 2 and this
        one is in layer 4. Two spellings of one query: change them together.
        """
        stmt = (
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(1)
        )
        return (await self._session.scalars(stmt)).first()

    async def messages_before(
        self, conversation_id: uuid.UUID, at: datetime, *, limit: int
    ) -> list[Block]:
        """The ``limit`` messages said in the conversation just before ``at``,
        oldest first: what was being talked about then."""
        stmt = (
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.kind == BlockKind.message,
                Block.created_at < at,
            )
            .order_by(Block.created_at.desc(), Block.id.desc())
            .limit(limit)
        )
        return list(reversed(list((await self._session.scalars(stmt)).all())))

    async def last_messages(
        self, conversation_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, Block]:
        """The last message said in each of these conversations, keyed by it;
        a conversation nothing was said in is missing."""
        if not conversation_ids:
            return {}
        ranked = (
            select(
                Block.id,
                func.row_number()
                .over(
                    partition_by=Block.conversation_id,
                    order_by=(Block.created_at.desc(), Block.id.desc()),
                )
                .label("rank"),
            )
            .where(
                Block.conversation_id.in_(conversation_ids),
                Block.kind == BlockKind.message,
            )
            .subquery()
        )
        rows = await self._session.scalars(
            select(Block)
            .join(ranked, ranked.c.id == Block.id)
            .where(ranked.c.rank == 1)
        )
        return {block.conversation_id: block for block in rows}

    async def between(
        self, conversation_id: uuid.UUID, since: datetime, until: datetime
    ) -> list[Block]:
        """Everything in the conversation from ``since`` to ``until``, both
        included, oldest first: messages, the files sent with them and the lines
        the platform wrote among them."""
        stmt = (
            select(Block)
            .where(
                Block.conversation_id == conversation_id,
                Block.created_at >= since,
                Block.created_at <= until,
            )
            .order_by(Block.created_at, Block.id)
        )
        return list((await self._session.scalars(stmt)).all())

    async def turn_starts(
        self, conversation_id: uuid.UUID
    ) -> list[tuple[datetime, uuid.UUID]]:
        """When each of this conversation's turns began, oldest first.

        One conversation's own blocks: a task's turns sit under the task's own
        id, and folding them into its room's would attribute the same turn
        twice. A turn lands several blocks, so its start is the earliest of
        them; the metering proxy's log carries no work id and attaches each
        call to whichever turn was under way at its timestamp.
        """
        stmt = (
            select(Block.turn_id, func.min(Block.created_at))
            .where(
                Block.conversation_id == conversation_id,
                Block.turn_id.is_not(None),
            )
            .group_by(Block.turn_id)
            .order_by(func.min(Block.created_at))
        )
        rows = (await self._session.execute(stmt)).all()
        return [(started, work_id) for work_id, started in rows if work_id and started]
