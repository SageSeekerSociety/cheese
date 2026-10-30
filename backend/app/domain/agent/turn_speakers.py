"""一轮的点名：这一轮替谁在做事，记忆就读谁的 private、轮末对账也只收谁那一棵。

一轮只有一位：多人的房间里，别人的 private 不进这一轮（产品决定，2026-10-30）。
是谁按这个先后认：

- 这一轮窗口里**最后一位说话的人**——这一轮回答的是他；
- 没有人说话时，这一轮**替谁在做事的**那一位：周期任务那一轮的主人（他本人没在
  窗口里说过话，那一轮的内容是平台写完塞进投递的），任务自己的会话里任务的负责人
  （实况文档写给他看，按他的习惯写）；
- 私聊里那个人。

这个域不必认识周期任务：写投递的调度器知道主人是谁，把 `routineOwner` 留在
payload 里，认这一个键就够了。

（单独一个模块：`chat.py` 贴着体量上限，而这里全是读，不动任何会话状态。）
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.own import owned_instance
from app.domain.block.models import Block
from app.domain.delivery.models import Delivery
from app.domain.identity.handles import names_a_person
from app.domain.notice_meta import EVENT_ROUTINE_RUN


async def turn_speaker(
    session: AsyncSession,
    delivery_id: uuid.UUID | None,
    pending: Sequence[Block],
    private_owner: str | None,
    task_owner: str | None = None,
) -> str | None:
    """这一轮的那一位（见模块开头）；一位都认不出时 None，这一轮只有 team。

    只算**人**：private 是「人 × 项目」的那一份，队友手里的句柄在这里不是一个
    作用域，问了也只会问到一棵不存在的树（`names_a_person`）。
    """
    spoke = [b.author for b in pending if names_a_person(b.author)]
    if spoke:
        return spoke[-1]
    owner = await routine_owner(session, delivery_id)
    for handle in (owner, task_owner, private_owner):
        if handle and names_a_person(handle):
            return handle
    return None


async def routine_owner(
    session: AsyncSession, delivery_id: uuid.UUID | None
) -> str | None:
    """这一轮如果是周期任务派下来的，规则主人是谁；不是就 None。

    从**投递那一笔**上认，不是在历史里找：那一轮没有主人署名的消息（内容是
    平台写完塞进投递的），而写它的调度器知道主人是谁 —— 它把 `routineOwner`
    留在 payload 里。

    delivery 行不在（老投递、另一台机器刚清的账）时按「不是周期任务的一轮」
    处理：少读一个人那一份索引，不影响这一轮能不能跑。
    """
    if delivery_id is None:
        return None

    row = await session.get(Delivery, delivery_id)
    payload = (row.payload if row is not None else None) or {}
    if payload.get("eventType") != EVENT_ROUTINE_RUN:
        return None
    owner = payload.get("routineOwner")
    return owner if isinstance(owner, str) and owner else None


async def is_routine_run(session: AsyncSession, delivery_id: uuid.UUID | None) -> bool:
    """这一轮是不是周期任务的一次执行：同样从那一笔投递上认。那一轮在支线里
    也可以写（存它的结果），之后有人追问的那几轮照支线的规矩只读。"""
    if delivery_id is None:
        return False

    row = await session.get(Delivery, delivery_id)
    payload = (row.payload if row is not None else None) or {}
    return payload.get("eventType") == EVENT_ROUTINE_RUN


async def private_memory_for(
    session: AsyncSession, instance_id: uuid.UUID, speaker: str | None
) -> str | None:
    """Whose private memory this agent's turn may carry: ``speaker``'s, unless
    the agent's session runs on a person's machine.

    A member's own agent whose model connection is on their machine runs its
    session there (`owner_provider`). That machine is a person's: one account
    runs everything on it, a command the session runs may run on it with no
    sandbox, and its owner reads its disk. Nothing there can keep one person's
    private memory from another person's turn, so such an agent carries none,
    neither the pointer to the index nor the tree, and the shared memory only.
    Every other session runs on the platform's session host, kept to its own
    seat (`remote_execution/namespace._keep_only`)."""
    owned = await owned_instance(session, instance_id)
    return speaker if owned is None or owned.on_account else None
