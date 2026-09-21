"""把一条提议交出去 —— 闸门那一半是纯的，这一半是 I/O。

提议不是一种新东西，所以这里没有新的表、新的收件箱、新的通道：它就是房间里一条
「下一步在人手上」的事件，走 `delivery/addressing.py` 的寻址和 `delivery/ledger.py`
的账本（结论 40 后半）。`announce()` 已经把这两步接在一起了，这里只填三样：

- 说什么 —— 闸门算好的那一句（`Proposal.content`），房间里和通知里是同一句；
- 下一步在谁手上 —— `who=human`，所以它真的会发出去；
- 点了谁的名 —— `asked=approver`，因为这件事**停在他这里**：在他点头之前没有任何
  一方能往下走。这正是「被问的那个人」那一档的定义（`addressing.REASON_ASKED`）。

`severity=warn` 而不是 `error`：调用没发生，但也没有出错。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.announce import announce
from app.domain.agent.platform_notices import (
    EVENT_POLICY_PROPOSAL,
    SEVERITY_WARN,
    WHO_HUMAN,
    notice,
)
from app.domain.block.models import Block
from app.domain.delivery.addressing import Event
from app.domain.policy.gate import Proposal


async def propose(
    session: AsyncSession, proposal: Proposal, *, place_id: uuid.UUID
) -> Block | None:
    """把这条提议落进房间并投给要点头的那个人。

    写的是调用方的 session：提议和「这次调用没有发生」必须一起提交，否则回滚之后
    房间里留着一条提议，而拦下它的那次调用早已照常跑掉了。
    """
    return await announce(
        session,
        place_id=place_id,
        content=proposal.content,
        meta=notice(
            EVENT_POLICY_PROPOSAL,
            severity=SEVERITY_WARN,
            who=WHO_HUMAN,
            detail=(
                f"资源：{proposal.call.label}（{proposal.call.subject}）\n"
                f"档位：{proposal.call.tier}\n"
                f"发起：{proposal.asked_by}"
            ),
            detail_label="提议详情",
        ),
        points_at=Event(asked=proposal.approver),
    )
