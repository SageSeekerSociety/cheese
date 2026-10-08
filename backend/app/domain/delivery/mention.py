"""点名一位 AI 队友，就是给它的席位记一条投递。

不变量 I13 说「没人被点名，谁都不动 —— 这句对人对 agent 是同一句」。人那一侧早就
成立：人 @ 了谁，那一位就起一轮。反过来那一半一直是空的：agent 用 `chat_send` 发的
消息里 @ 了另一位队友，消息落库、通知也写了，被点名的那位却什么都收不到，因为发布
路由从来不起一轮。一间房坐着几位队友，它们之间唯一能说话的地方是房间（`delivery/
note.py`：不同 handle 之间只走 chat），而这条路是单向的。

这里不新造一条起轮次的路径：点名记成投递账本里的一行（`delivery/agent.py` 的
`record_agent`），和定时投递、周期任务是同一本账、同一个派发器。账本按「事件 × 席位」
去重，所以同一条消息被重试发布，被点名的人也只被叫一次。

人发的消息也经过这里：`post_user_message` 那条路一条消息只起**一位**的一轮（它的
`agent_recipient` 是单数），同一条消息里点到的其余几位以前就这么丢了。它们现在也记
成投递。

## 熔断

两位 agent 互相 @ 会一来一回叫下去，每一下都是一轮计费的模型调用。人发的点名不会
自己成环，所以只数 agent 发起的那一种：同一间房一小时里最多
`AGENT_MENTIONS_PER_HOUR` 次，超出的那次不叫醒对方，房间里落一行提示 —— 静默吞掉
和一直叫下去一样看不见。
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent_instance.own import owned_instance
from app.domain.block.quoted_context import quoted_context_prompt
from app.domain.conversation.services import of_room
from app.domain.delivery.agent import instance_for_seat, now, record_agent
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.delivery.models import Delivery
from app.domain.notification.models import NotificationType
from app.domain.topic_membership.services import TopicMemberService

#: 同一间房一小时里，agent 点名能叫起几轮。人点名不受它限制。
AGENT_MENTIONS_PER_HOUR: Final = 20

#: 投递 payload 里的 `eventType`：谁发的点名。熔断只数 agent 发起的那一种。
BY_AGENT: Final = "agent_mention"
BY_PERSON: Final = "person_mention"

_MENTION = re.compile(r"<@([\w-]+)>")


def mentioned_handles(text: str) -> list[str]:
    """``text`` 里 ``<@handle>`` 点到的 handle，按出现顺序去重。纯函数。"""
    return list(dict.fromkeys(_MENTION.findall(text or "")))


def mention_prompt(*, author: str, message: str, block_id: uuid.UUID) -> str:
    """被点名的那位队友这一轮读到的话。

    消息本身在房间时间线上，这里再带一份原文，是因为这一轮的 prompt 就是它：不带的
    话，它得先去翻聊天记录才知道自己为什么醒了。后半句是防环的第一道：只回一句「收
    到」还点对方的名，就又叫起对方一轮。
    """
    return (
        f"{author} 在房间里点了你的名（消息 id `{block_id}`）：\n\n"
        f"---\n{message}\n---\n\n"
        "按这条消息处理，回话用 chat_send。点名会叫起对方的一轮：只是回个话、"
        "报个结果，不要点对方的名；要对方接着动手时才点。"
    )


@dataclass
class Summoned:
    """一条消息点名的结果：叫醒了谁，因为熔断没叫醒谁。"""

    woken: list[str] = field(default_factory=list)
    fused: list[str] = field(default_factory=list)


async def _agent_mentions_since(
    session: AsyncSession, room_id: uuid.UUID, since: datetime
) -> int:
    """Agent-to-agent calls in the room this past hour, in any of its
    conversations: the fuse is the room's."""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Delivery)
            .where(
                of_room(Delivery.conversation_id, room_id),
                Delivery.recorded_at >= since,
                Delivery.payload["eventType"].as_string() == BY_AGENT,
            )
        )
        or 0
    )


async def record_mentions(
    session: AsyncSession,
    *,
    project_id: uuid.UUID,
    room_id: uuid.UUID,
    conversation_id: uuid.UUID,
    block_id: uuid.UUID,
    author: str,
    content: str,
    by_agent: bool,
    occurred_at: datetime,
    skip: frozenset[str] = frozenset(),
    quoted_context: dict | None = None,
) -> Summoned:
    """把 ``content`` 里点到的每一位 AI 队友记成一条投递。写调用方的 session，不提交。

    只认这间房名册上的 agent 席位：点到人是站内信的事（`announce_mentions`），点到
    不在房里的队友叫不起任何东西。作者自己不算 —— 点自己的名只会让这一轮之后再跑一
    轮。``skip`` 是调用方已经用别的办法叫起的席位（人发的消息里第一位点到的那个）。
    ``conversation_id`` 是被点到的队友回答的地方：消息所在的支线或任务，主线上的
    消息则是它那条支线（`thread.services.answered_in`）。
    """
    handles = mentioned_handles(content)
    if not handles:
        return Summoned()
    seats = set(await TopicMemberService(session).agent_handles(room_id))
    named = [h for h in handles if h in seats and h != author and h not in skip]
    summoned = Summoned()
    if not named:
        return summoned
    budget = None
    if by_agent:
        used = await _agent_mentions_since(session, room_id, now() - timedelta(hours=1))
        budget = AGENT_MENTIONS_PER_HOUR - used
    for seat in named:
        instance = await instance_for_seat(session, project_id, seat)
        if instance is None:
            continue
        # A member's own agent answers its owner alone (#2991): naming it from
        # anyone else, an agent included, reaches nobody.
        if await owned_instance(session, instance.id) is not None and (
            by_agent or not await _calls_own(session, instance.id, author)
        ):
            continue
        if budget is not None and budget <= 0:
            summoned.fused.append(seat)
            continue
        await record_agent(
            session,
            DeliveryEvent(
                id=block_id,
                type=NotificationType.MENTION,
                payload={
                    "projectId": str(project_id),
                    "topicId": str(room_id),
                    "eventType": BY_AGENT if by_agent else BY_PERSON,
                    "author": author,
                    "blockId": str(block_id),
                },
                occurred_at=occurred_at,
            ),
            conversation_id=conversation_id,
            instance_id=instance.id,
            content=mention_prompt(
                author=author,
                message=content + quoted_context_prompt(quoted_context),
                block_id=block_id,
            ),
        )
        if budget is not None:
            budget -= 1
        summoned.woken.append(seat)
    return summoned


async def _calls_own(
    session: AsyncSession, instance_id: uuid.UUID, author: str
) -> bool:
    # deferred-import: tests replace this name on app.domain.agent_instance.own
    from app.domain.agent_instance.own import owner_of

    owner = await owner_of(session, instance_id)
    return owner is not None and owner[0] == author
