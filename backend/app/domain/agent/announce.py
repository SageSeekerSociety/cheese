"""平台在房间里说一句话 —— 落成系统事件，需要人动手时同时通知到人。

`platform_notices` 定的是**说什么**：一行 `content` 加一份结构化 `meta`。这里定
的是**怎么说出去**，两件事在一次调用里完成：

1. 这句话落进房间的时间线（`kind=event, author_type=system`）；
2. `meta.who` 是 `human` 时，同一句话投给点名的人 —— 站内通知一条，邮件队列一条。

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


async def _notify(
    session: AsyncSession,
    *,
    place: Place,
    content: str,
    meta: dict,
    recipients: Sequence[str],
) -> None:
    handles = {h.strip() for h in recipients if h and h.strip()}
    if not handles:
        return
    if meta.get("who") != WHO_HUMAN:
        raise ValueError(
            "只有 who=human 的提示能点收件人 —— 另外两个码说的是平台或芝士正在"
            f"处理，不该惊动谁（event_type={meta.get('event_type')!r}）"
        )
    recipient_ids = set()
    for handle in sorted(handles):
        user = await user_by_handle(session, handle)
        if user is not None:
            recipient_ids.add(user.id)
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
