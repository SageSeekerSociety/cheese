"""本包为下面那些包声明的口子实现的那些函数。

`topic` 在第 2 层，够不着第 4 层的 `thread`，所以它在自己的 `ports.py` 里写下**自己
要的那一次查问**（`RoomThread`），函数留在这里，谁在门口谁递进去 —— 和
`agent_instance/adapters.py` 同一条规矩：口子按消费者分开写，实现按提供者放。

这里只做一件事：把 `Thread` 那一行的答案收成口子说好的内置类型。函数名跟着它实现
的端口叫，一个端口一个。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.thread.models import Thread


async def root_block_of(
    session: AsyncSession, conversation_id: uuid.UUID
) -> uuid.UUID | None:
    """`app.domain.topic.ports.RoomThread`：这条会话是支线的话，根消息是哪一条。

    一条支线自己就是一条会话（`conversations`，kind `thread`），`Thread.id` 就是
    那个会话 id —— 所以按会话 id 取这一行，取的正是「这条会话是不是支线」。
    """
    thread = await session.get(Thread, conversation_id)
    return None if thread is None else thread.root_block_id
