"""一间房时间线上最后一条 —— 停滞判据读的那一条。

`TopicService.stall_signal` 要回答「这间房是不是停在一个做到一半的动作上」，
判据的原料是最后一条块长什么样：谁写的、哪一种、`meta` 里是不是写着工具名。

从 `repositories.py` 拆出来时这条读法在 `block` 的仓库对象上
（`BlockRepository.latest_for_topic`）—— 那个对象在第 4 层，本包在第 2 层，
`topic -> block` 是 C5 压着的向上边之一。读的是数据不是那个包的代码，所以和
`statements.py` 里那三张裸表同一个写法：表在那里声明，读法在这里。

为什么不走口子：这条读没有别的调用方，也没有本包写一块时要的那些行为（轮次记账、
句子键、实时投递），而口子要在每个门口递一遍。写一条块走口子（
`topic/ports.py` 的 `PlatformLine`），读一条走裸表 —— 和 `unread.py`、
`replied.py` 一致。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.topic.repositories.statements import _blocks


@dataclass(frozen=True)
class LastWord:
    """最后一条块的几列，只取判读用得着的那几列。

    `kind` 和 `author_type` 是 `blocks` 列里存的那两个短字符串，不是
    `block.models` 的枚举成员 —— 本包看不见那个模块。要拿它们和码比，码在
    `statements.py` 里逐字写着（`KIND_ARTIFACT`、`KIND_EVENT`）。
    """

    id: uuid.UUID
    kind: str
    author_type: str
    author: str
    meta: dict[str, Any] | None
    created_at: datetime


async def last_word(
    session: AsyncSession, conversation_id: uuid.UUID
) -> LastWord | None:
    """这间房时间线上最新的一条，空房间回 ``None``。

    和 `BlockRepository.latest_for_topic` 同一条语句：按 ``created_at DESC,
    id DESC`` 取一条，不排除任何一种块 —— 「这间房最后一条」对读的人就是时间线
    最底下那一条，停滞判据立在上面才查得动。同一句 SQL 的两种写法，改一处就得
    改另一处（`block/history.py` 那边也挂着这句话）。
    """
    row = (
        await session.execute(
            select(
                _blocks.c.id,
                _blocks.c.kind,
                _blocks.c.author_type,
                _blocks.c.author,
                _blocks.c.meta,
                _blocks.c.created_at,
            )
            .where(_blocks.c.conversation_id == conversation_id)
            .order_by(_blocks.c.created_at.desc(), _blocks.c.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return LastWord(
        id=row.id,
        kind=row.kind,
        author_type=row.author_type,
        author=row.author,
        meta=row.meta,
        created_at=row.created_at,
    )
