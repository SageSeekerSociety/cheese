"""平台在房间里说一句话 —— 落成系统事件，需要人动手时同时通知到人。

`platform_notices` 定的是**说什么**：一行 `content` 加一份结构化 `meta`。这里定
的是**怎么说出去**，两件事在一次调用里完成：

1. 这句话落进房间的时间线（`kind=event, author_type=system`）；
2. 寻址结果点到的那些人各收到同一句话。

芝士自己的提问走 `notify_question`：那条消息已经在时间线上，只缺投递这一半。

通知里的文字就是房间里那一行，没有第二套措辞：人在通知里读到的和回房间看到的是
同一句，同一件事不会有两种说法。再加一个渠道（浏览器推送之类）也只改这一个函数。

**入参是一次寻址结果，不是一句文案加一串名字（I11）。** 谁该收到由
`delivery/addressing.py` 的 `address()` 答，这里不猜也不推：凭房间名册推一批收件人出
来，等于把一条多数人不该收的通知发给一屋子人。下一步在平台手上的那些事件，寻址结果
里本来就没有人，所以它们只在房间里留话 —— 以前这一条靠读 `meta.who` 的码当闸门，一
个给前端看的显示码兼着决定收件人，那就是同一个问题的第三个答案。

怎么送到是 `identity/arrival.py` 的事：人走站内信，agent 在自己房间的时间线上读到上
面刚落下的那一行。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.platform_notices import SEVERITY_INFO
from app.domain.block.about import EventAbout, landing
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.addressing import NOBODY, Addressed
from app.domain.identity.arrival import Arrival, how_it_arrives
from app.domain.notification.models import NotificationType
from app.domain.notification.publisher import publish_notification_event
from app.domain.room_task.place import Place, PlaceResolver
from app.domain.user.services import user_by_handle


async def announce(
    session: AsyncSession,
    *,
    place_id: uuid.UUID,
    content: str,
    meta: dict | None = None,
    author: str = "system",
    turn_id: uuid.UUID | None = None,
    addressed: Addressed = NOBODY,
) -> Block | None:
    """把 `content` 说进房间，并投给这条事件点到的那些人。

    写的是调用方的 session，不是自己开一个：卡、房间里那句话、通知三者一起提交，
    所以一次回滚不会留下「房间说递了卡，卡却不存在」。要跨事务活下来的调用点
    （合并结果已经在 GitHub 上发生了，房间必须知道）走
    `webhook.service.post_with_retries`，它每次重试开一个新 session 再调这里。

    返回落下的 block；房间已经不在了返回 None。
    """
    place = await PlaceResolver(session).resolve(place_id)
    if place is None:
        return None
    landed = landing(
        EventAbout.room,
        project_id=place.project_id,
        room_id=place.room_id,
    )
    block = await BlockRepository(session).add(
        project_id=landed.project_id,
        topic_id=landed.topic_id,
        task_id=landed.task_id,
        author=author,
        author_type=AuthorType.system,
        content=content,
        kind=BlockKind.event,
        turn_id=turn_id,
        meta=meta,
    )
    await _notify(
        session,
        place=place,
        content=content,
        meta=meta or {},
        addressed=addressed,
    )
    return block


async def notify_question(
    session: AsyncSession,
    *,
    place: Place,
    block_id: uuid.UUID,
    question: str,
    asker: str,
    addressed: Addressed,
) -> None:
    """芝士提出待确认问题，本轮停止等待 —— 通知等这个回答的人。

    **不走 `announce`。** 提问本身就是时间线上那条消息（`kind=message`，作者是
    芝士），再 announce 一次等于同一件事在房间里说两遍。所以这里只做投递这一半。

    也不用 `ROOM_NOTICE`：那个码说的是「平台在房间里说的一行」，最长 40 字、措辞
    由平台决定。这一条是芝士自己的话，长度取决于它怎么问，两者不是一种东西 ——
    共用一个码，前端就无法区分该按哪一种渲染。

    收件人同样来自寻址结果，理由和 `announce` 一致：等这个回答的只有一个人，而房间
    里还有其他成员。
    """
    recipient_ids = await _mailbox_ids(session, addressed)
    if not recipient_ids:
        return
    await publish_notification_event(
        session,
        recipient_ids=recipient_ids,
        type_=NotificationType.CHEESE_QUESTION,
        payload={
            "projectId": str(place.project_id),
            "topicId": str(place.room_id),
            "topicTitle": place.title,
            "question": question,
            "asker": asker,
            # 提问固定在对话末尾（本轮停在它这里），所以进入房间即可看到 ——
            # 这个 id 留给「定位到该条消息」用，当前不依赖它也能找到。
            "blockId": str(block_id),
        },
    )


async def _mailbox_ids(session: AsyncSession, addressed: Addressed) -> set[int]:
    """寻址结果里走站内信的那些人 → 真实用户 id。

    agent 那一档不落在这里：它在自己房间的时间线上读到这条事件，往它的收件箱里塞一
    行写的是一条谁都不会打开的记录（`identity/arrival.py`）。

    人解析不到用户行是常态而非错误：`reporter_handle` 可能是外部提交的一个名字。少
    发一条通知，好于为一个不存在的人抛错。
    """
    ids: set[int] = set()
    people = sorted(
        {
            r.handle
            for r in addressed.recipients
            if how_it_arrives(r.handle) is Arrival.mailbox
        }
    )
    for handle in people:
        user = await user_by_handle(session, handle)
        if user is not None:
            ids.add(user.id)
    return ids


async def _notify(
    session: AsyncSession,
    *,
    place: Place,
    content: str,
    meta: dict,
    addressed: Addressed,
) -> None:
    recipient_ids = await _mailbox_ids(session, addressed)
    if not recipient_ids:
        return
    await publish_notification_event(
        session,
        recipient_ids=recipient_ids,
        # 一个类别码走完所有平台提示，具体是哪件事看 `eventType` —— 通知要显示的
        # 那句话是后端给的 `content`，不是前端按类别码拼出来的模板。
        type_=NotificationType.ROOM_NOTICE,
        payload={
            "projectId": str(place.project_id),
            "topicId": str(place.room_id),
            "topicTitle": place.title,
            "content": content,
            "eventType": str(meta.get("event_type") or ""),
            "severity": str(meta.get("severity") or SEVERITY_INFO),
        },
    )
