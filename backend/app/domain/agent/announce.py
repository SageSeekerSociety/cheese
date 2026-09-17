"""平台在房间里说一句话 —— 落成系统事件，需要人动手时同时通知到人。

`platform_notices` 定的是**说什么**：一行 `content` 加一份结构化 `meta`。这里定
的是**怎么说出去**，两件事在一次调用里完成：

1. 这句话落进房间的时间线（`kind=event, author_type=system`）；
2. `meta.who` 是 `human` 时，同一句话投给点名的人 —— 站内通知一条，邮件队列一条。

芝士自己的提问走 `notify_question`：那条消息已经在时间线上，只缺投递这一半。

通知里的文字就是房间里那一行，没有第二套措辞：人在通知里读到的和回房间看到的是
同一句，同一件事不会有两种说法。再加一个渠道（浏览器推送之类）也只改这一个函数。

**收件人由调用点给，这个函数不猜。** 「要人来」只说了要人，没说要哪个人：一张验收
卡知道自己递给了谁，一个挂掉的运行环境只知道自己在哪个房间。凭房间名册推一批收件
人出来，等于把一条多数人不该收的通知发给一屋子人；所以不点名的调用点只在房间里留
话，和这个函数存在之前完全一样。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.agent.platform_notices import SEVERITY_INFO, WHO_HUMAN
from app.domain.block.models import AuthorType, Block, BlockKind
from app.domain.block.repositories import BlockRepository
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
    recipients: Sequence[str] = (),
) -> Block | None:
    """把 `content` 说进房间，并按 `meta.who` 决定要不要通知 `recipients`。

    写的是调用方的 session，不是自己开一个：卡、房间里那句话、通知三者一起提交，
    所以一次回滚不会留下「房间说递了卡，卡却不存在」。要跨事务活下来的调用点
    （合并结果已经在 GitHub 上发生了，房间必须知道）走
    `webhook.service.post_with_retries`，它每次重试开一个新 session 再调这里。

    返回落下的 block；房间已经不在了返回 None。
    """
    place = await PlaceResolver(session).resolve(place_id)
    if place is None:
        return None
    block = await BlockRepository(session).add(
        project_id=place.project_id,
        topic_id=place.room_id,
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
        recipients=recipients,
    )
    return block


async def notify_question(
    session: AsyncSession,
    *,
    place: Place,
    block_id: uuid.UUID,
    question: str,
    asker: str,
    recipients: Sequence[str],
) -> None:
    """芝士提出待确认问题，本轮停止等待 —— 通知等这个回答的人。

    **不走 `announce`。** 提问本身就是时间线上那条消息（`kind=message`，作者是
    芝士），再 announce 一次等于同一件事在房间里说两遍。所以这里只做投递这一半。

    也不用 `ROOM_NOTICE`：那个码说的是「平台在房间里说的一行」，最长 40 字、措辞
    由平台决定。这一条是芝士自己的话，长度取决于它怎么问，两者不是一种东西 ——
    共用一个码，前端就无法区分该按哪一种渲染。

    收件人由调用点给出，理由和 `announce` 一致：等这个回答的只有一个人，而房间里
    还有其他成员。
    """
    recipient_ids = await _recipient_ids(session, recipients)
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


async def _recipient_ids(session: AsyncSession, recipients: Sequence[str]) -> set[int]:
    """handle → 真实用户 id。解析不到的 handle 不出现在结果里。

    解析不到是常态而非错误：`reporter_handle` 可能是外部提交的一个名字，芝士自身
    也有 handle。少发一条通知，好于为一个不存在的人抛错。
    """
    ids: set[int] = set()
    for handle in sorted({h.strip() for h in recipients if h and h.strip()}):
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
    recipients: Sequence[str],
) -> None:
    if not any(h and h.strip() for h in recipients):
        return
    if meta.get("who") != WHO_HUMAN:
        raise ValueError(
            "只有 who=human 的提示能点收件人 —— 另外两个码说的是平台或芝士正在"
            f"处理，不该惊动谁（event_type={meta.get('event_type')!r}）"
        )
    recipient_ids = await _recipient_ids(session, recipients)
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
