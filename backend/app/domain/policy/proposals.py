"""把一条提议交出去 —— 闸门那一半是纯的，这一半是 I/O。

提议不是一种新东西，所以这里没有新的表、新的收件箱、新的通道：它就是房间里一条
「下一步在人手上」的事件，走 `delivery/addressing.py` 的寻址和 `delivery/ledger.py`
的账本（结论 40 后半）。`announce()` 已经把这两步接在一起了，这里只填三样：

- 说什么 —— 闸门算好的那一句（`Proposal.content`），房间里和通知里是同一句；
- 下一步在谁手上 —— `who=human`，所以它真的会发出去；
- 点了谁的名 —— `asked=approver`，因为这件事**停在他这里**：在他点头之前没有任何
  一方能往下走。这正是「被问的那个人」那一档的定义（`addressing.REASON_ASKED`）。

`severity=warn` 而不是 `error`：调用没发生，但也没有出错。

## 一条提议的身份是它提的那件事，不是它那一行

撞上策略的调用会**反复**发生 —— 房间里每来一条消息就解析一次这一轮的模型和机器，
每一次都撞同一堵墙。人要收到的只有一条（结论 15 / 不变量 I11「一次」）。

所以这里不让 block 当身份：block 每次新建，拿它当去重键就等于没有去重，房间时间线
和收件箱会被同一句话刷屏。身份由「在哪个房间、要什么资源、哪一档、等谁点头」算出
来（`uuid5`），同一件事被问第一百遍也还是这一个 id：房间里已经有这条提议就不再落
第二条，投递账本也按它去重（结论 58「去重键跟事件」）。

策略放宽了，这次调用直接放行，根本走不到这里；换了别的资源或别的档位，那是另一件
事、另一个 id、另一条提议 —— 这正是「身份跟着那件事走」该有的样子。
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
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
from app.domain.policy.gate import Call, Proposal
from app.domain.room_task.place import PlaceResolver

#: 提议 id 的命名空间。固定值，因为身份要跨进程、跨重启算得出同一个。
_NAMESPACE = uuid.UUID("6f1f2c1e-6a3b-5c2d-9a47-7b0d3e5a1c64")

#: 提议身份落在 `meta` 的这个键上，房间里那条事件靠它被认出来。
META_PROPOSAL_ID = "proposal_id"


def identity(place_id: uuid.UUID, call: Call) -> uuid.UUID:
    """这条提议的身份：在哪个房间，要什么，哪一档，等谁点头。"""
    return uuid.uuid5(
        _NAMESPACE,
        f"{place_id}:{call.resource}:{call.subject}:{call.tier}:{call.approver}",
    )


async def propose(
    session: AsyncSession, proposal: Proposal, *, place_id: uuid.UUID
) -> Block | None:
    """把这条提议落进房间并投给要点头的那个人；已经提过就什么也不做。

    写在传进来的这个 session 上，**提交由调用方负责**，而调用方欠的是一条：提议落
    库之后这次调用必须中止。两个方向不一样——提议在、调用没发生，那只是白问了一
    次；调用发生了却还留着一条「等谁点头」，那就是先斩后奏。所以要求的是后者不能
    发生，而不是两者非得在同一个事务里。

    两个调用点都用手里那条事务：`PUT /topics/{id}/compute-profile` 是请求自己的
    session，提议和「绑定没写」一起提交；轮次组装（`agent/chat.py` 的
    `_pass_policy_gate`）写在组装那条事务上，提交完这一轮就以这条提议收场——机器
    没绑、请求没发，往下那些占用一样也没发生。
    """
    place = await PlaceResolver(session).resolve(place_id)
    if place is None:
        return None
    proposal_id = identity(place.room_id, proposal.call)
    already = await session.scalar(
        select(Block.id)
        .where(
            Block.topic_id == place.room_id,
            Block.meta[META_PROPOSAL_ID].as_string() == str(proposal_id),
        )
        .limit(1)
    )
    if already is not None:
        return None
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
        )
        | {META_PROPOSAL_ID: str(proposal_id)},
        points_at=Event(asked=proposal.approver),
        event_id=proposal_id,
    )
