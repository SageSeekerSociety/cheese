"""同 handle 便条的写侧（结论 11，不变量 I14②）。

## 它是什么

同一个 handle 在一个项目里是**一个参与者**，它在各个地点上的那些会话是它的线程。
线程之间要说话，走的不是 chat —— chat 是说给房间里的人看的，而一条线程告诉另一条
线程「那份数据我清完了，口径按新的来」不是房间里的事。所以线程之间走**内部便条**。

读侧的通道早就在用：`chat.notify_running_turn` 把一句话直接递进那条线程**正在跑的
那一轮**，不进时间线（今天文档变更就是这么递的）。这个模块是写侧 —— 以前没有，一
条线程想给另一条线程留话，只能在房间里说，于是房间里堆的是两条线程互相对暗号。

## 收件人只能是自己的另一条线程

**不同 handle 之间只走 chat，agent 对 agent 也是**（结论 12）。没有第二条私下通道：
两个 agent 要说话就在房间里说，人看得见 —— 这是「安全靠可见」在多 agent 下的直接
推论。所以这里的守卫不是一句提醒，是这条通道成立的条件：收件人那条线程的席位必须
和发件人是同一个 handle，不是就拒，连「它可能是同一个人开的第二个号」这种判断都不
做。

守卫读的是**名册**，不是调用方说自己是谁：调用方给的是一条线程的 id，同不同 handle
由平台拿两边的席位比出来。让调用方声明收件人 handle，等于把这条守卫交给被守的那一
方去执行。
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
    seat = await TopicMemberService(session).addressable_agent_handle(target.room_id)
    if seat != sender:
        raise ValidationError(NOT_YOUR_OWN_THREAD)
    return await chat.notify_running_turn(target.room_id, content)
