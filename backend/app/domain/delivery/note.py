"""Private notes between active threads of the same project agent.

Membership allows addressing the room; the active turn's immutable agent seat
and expected work ID determine whether it may receive this note. An idle or
different agent's thread does not receive it, and no timeline row or new turn
is created. Communication between different handles remains visible chat.
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, ValidationError
from app.domain.agent.chat import ChatService
from app.domain.topic.services import TopicService
from app.domain.topic_membership.services import TopicMemberService

#: 送给别的 handle 时说的那一句。说清楚还有哪条路可走 —— 收件人不对不是故障，是这
#: 条通道本来就只通向自己。
NOT_YOUR_OWN_THREAD = (
    "便条只能留给同一个 handle 的另一条线程。跟别的参与者说话走房间里的 chat，"
    "agent 对 agent 也是。"
)


async def send_note(
    session: AsyncSession,
    chat: ChatService,
    *,
    sender: str,
    from_project_id: uuid.UUID,
    to_thread: uuid.UUID,
    content: str,
) -> bool:
    """把一张便条递给同一个 handle 的另一条线程。返回那条线程有没有接住。

    没接住（那边这一刻没有在跑的轮次）不是错误：便条是递给一条**正在跑**的线程的，
    那边空着的时候没有人要被打断，如实回一个 False。
    """
    if not content.strip():
        raise ValidationError("便条不能是空的")
    try:
        target = await TopicService(session).place_or_404(to_thread)
    except NotFoundError:
        raise ValidationError(NOT_YOUR_OWN_THREAD) from None
    if target.project_id != from_project_id:
        # 一个 handle 是**一个项目里**的一个参与者：跨项目的同名席位不是同一条线程
        # 上的自己，它读的记忆、能看见的东西都是另一套。
        raise ValidationError(NOT_YOUR_OWN_THREAD)
    seats = await TopicMemberService(session).agent_handles(target.room_id)
    if sender not in seats:
        raise ValidationError(NOT_YOUR_OWN_THREAD)
    return await chat.notify_running_turn(
        target.room_id, content, recipient_seat=sender
    )
