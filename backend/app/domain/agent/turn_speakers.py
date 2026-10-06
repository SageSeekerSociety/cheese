"""一轮的点名：这一轮读的是谁的口味，轮末对账就收谁那一棵。

名单在 `_assemble_turn` 里定：窗口里说话的这几位，加上这一轮**替谁在做事的**
那一位。周期任务那一轮的主人就是后者 —— 他本人没在窗口里说过话（那一轮的内容
是平台写完塞进投递的，没有一条他自己署名的消息），可这一轮跑的是他交代的活。
只读不收的话，他这一轮改过的偏好下一轮还是旧的。

这个域不必认识周期任务：写投递的调度器知道主人是谁，把 `routineOwner` 留在
payload 里，认这一个键就够了。

（单独一个模块：`chat.py` 贴着体量上限，而这里全是读，不动任何会话状态。）
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.platform_notices import EVENT_ROUTINE_RUN
from app.domain.block.models import Block
from app.domain.identity.handles import names_a_person


async def turn_speakers(
    session: AsyncSession,
    delivery_id: uuid.UUID | None,
    pending: Sequence[Block],
    private_owner: str | None,
) -> tuple[str, ...]:
    """本轮说话的这几位（去重，按第一次出现的先后），记忆按这份名单读、也按它收。

    只算**人**：private 是「人 × 项目」的那一份，队友手里的句柄在这里不是一个
    作用域，问了也只会问到一棵不存在的树（`names_a_person`）。
    """
    owner = await routine_owner(session, delivery_id)
    return tuple(
        dict.fromkeys(
            handle
            for handle in (
                *(b.author for b in pending),
                *((private_owner,) if private_owner else ()),
                *((owner,) if owner else ()),
            )
            if names_a_person(handle)
        )
    )


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
    from app.domain.delivery.models import Delivery

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
    from app.domain.delivery.models import Delivery

    row = await session.get(Delivery, delivery_id)
    payload = (row.payload if row is not None else None) or {}
    return payload.get("eventType") == EVENT_ROUTINE_RUN
