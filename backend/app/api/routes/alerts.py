"""项目收件箱的路由 —— spec §8.5/8.6, evals G2/G3.

横跨两个资源前缀（项目下的集合 + 单条通知上的动作），所以这个 router 用空前缀，
每条路径写全。

每一条读写某个人信箱的端点都经 ``ActorResolver.resolve_recipient`` 认收件人 ——
那条规则住在信任边界上（app.api.auth），不在这里，所以下一个按收件人取信的端点
绕不过它。``target_handle`` 永远只是一句待核对的断言，不是身份本身。

路径上仍然叫 ``alerts``：URL 是对外的契约，而读写的表已经是 `notification` ——
平台报告自己的那些和人对人的那些同住一张收件箱（结论 58）。

Every route here that writes commits before it answers. ``get_db`` commits in
its teardown, which FastAPI runs after the response has been sent, so a client
that reads the inbox as soon as it hears "done" can still see the old row: a
reminder collapsed with 收起 comes straight back on the next inbox read.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.deps import get_broker
from app.api.response import ok, page
from app.auth.project_access import may_read_project
from app.core.db import get_db
from app.core.errors import NotFoundError, ValidationError
from app.core.sentences import say
from app.domain.agent.staleness import announce_stale
from app.domain.notification.models import Notification
from app.domain.notification.schemas import (
    PROJECT_NOTIFICATION_KINDS,
    NotificationCreate,
    NotificationOut,
    ResolveIn,
)
from app.domain.notification.services import ProjectNotificationService
from app.domain.topic.services import TopicService

router = APIRouter(prefix="", tags=["alerts"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _dump(row: Notification) -> dict:
    return NotificationOut.from_row(row).model_dump(mode="json")


async def _acting_recipient(resolver: ActorResolver, notification: Notification) -> str:
    """能对这一条动手的那个验证过的调用者：它的收件人。

    并表之前还有第二档 —— 一条广播（没有收件人）任何验证过的调用者都能动。广播
    现在在写入时就展开成一人一行，所以这里只剩一句话。
    """
    return await resolver.resolve_recipient(
        requested=notification.recipient_handle,
        project_id=notification.project_id,
        allow_anonymous=False,
    )


@router.post("/projects/{project_id}/alerts")
async def create_notification(
    project_id: uuid.UUID,
    body: NotificationCreate,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """往项目的收件箱里写 —— agent（带 scope 的令牌）或者一个登录了的人；匿名的进
    不来。

    凭据之外还要看两头的成员身份。写的人：这个项目（指了房间就是那个房间）的成员
    —— 否则任何登录了的人都能往一个他不在的项目里、给每个成员的收件箱写一条标题和
    正文都由他定的消息。收的人：同样打得开这个项目或房间 —— 打不开的人读不到它，
    一条到不了任何人的通知不是成功；指着私密房间的那一条，也不能把字给房间外面的
    人看。房间得是这个项目的。全局 sandbox token 是开发用的受信覆盖，和
    ``authorize_project`` 一样不问成员。

    返回的是**一组**：一条广播在这里就展开成一人一行，所以「刚写下的那一条」不再
    只有一个答案。
    """
    if body.kind not in PROJECT_NOTIFICATION_KINDS:
        raise ValidationError(say("alertKindNotFromInbox"))
    actor = await resolver.require_verified_caller(
        project_id=project_id, topic_id=body.topic_id
    )
    # 这条通知关于哪条对话 —— 一个频道自己那条线、它的一条任务、或一条支线。调用
    # 点手上那个地点 id（`$CHEESE_TOPIC`，芝士在自己房间里就是那条任务或那条支线）
    # 本来就是它，校验完照原样存下来。折成它所在的频道会让在任务或支线里问的决策
    # 答到频道主线上：拍板的回执（`notification.ProjectNotificationService.resolve`）
    # 照着这一列写，提问的那条会话于是读不到自己等的那句话。
    conversation_id = None
    room_id = None
    if body.topic_id is not None:
        place = await TopicService(db).place_or_404(body.topic_id)
        if place.project_id != project_id:
            raise NotFoundError("Topic not found")
        conversation_id = place.conversation_id
        room_id = place.room_id
    if actor.authenticated:
        # 授权、名册和面板都是频道的，所以下面这几句问的是 `room_id`。
        if room_id is not None:
            await resolver.authorize_topic(
                actor, project_id=project_id, topic_id=room_id, enforce=True
            )
        elif actor.via == "token":
            # A session token names a person and no project, so the project is
            # asked. An agent credential is minted for one project and `resolve`
            # has already refused it everywhere else: its scope is the answer.
            await resolver.authorize_project(actor, project_id=project_id)
        target = body.target_handle
        if target is not None and not (
            await resolver.topic_admits_handle(
                actor, project_id=project_id, topic_id=room_id, handle=target
            )
            if room_id is not None
            else await may_read_project(db, project_id=project_id, handle=target)
        ):
            raise ValidationError(say("alertRecipientOutside"))
    rows = await ProjectNotificationService(db).create(
        project_id=project_id,
        level=body.level,
        kind=body.kind,
        title=body.title,
        body=body.body,
        target_handle=body.target_handle,
        conversation_id=conversation_id,
        payload=body.payload,
    )
    await db.commit()
    if room_id is not None:
        await announce_stale(room_id, "notify")
    return ok(page([_dump(row) for row in rows], len(rows)))


@router.get("/projects/{project_id}/alerts")
async def list_notifications(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
    unread_only: bool = False,
    page_size: int = Query(default=50, ge=1, le=100),
    page_start: int | None = None,
) -> dict:
    """这个调用者在这个项目里的信，新的在前，一页一页地给。

    这条读以前没有上界：一个项目跑久了，整个历史上的信会一次全拉回来。现在默认
    一页最多 ``page_size`` 条（上限 100），`has_more` 说还有没有，`next_start` 说
    下一页从哪一行开始 —— 把它原样当成下一次的 `page_start` 就能一路读到底，一行
    不漏（键集游标：收件箱的头一直在长，offset 会在翻页时漏行重行）。

    `total` 是真总数，不是这一页的条数。分页之前它是 `page(len(items))`，也就是
    「这一页有几条」，一加上限这两个数就分家了。

    写法照本仓的分页协议（`design/common/parameters.yaml` 的 ``page_start`` /
    ``page_size``，`design/common/responses.yaml` 的 ``Page``），和相邻的
    ``GET /topics/{id}/blocks`` 同一套：游标是行号、`has_more` 平铺在 ``data`` 里。
    """
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    items, total, next_start = await ProjectNotificationService(db).list_for_project(
        project_id,
        target_handle=handle,
        unread_only=unread_only,
        page_size=page_size,
        page_start=page_start,
    )
    return ok(
        {
            **page([_dump(n) for n in items], total),
            "has_more": next_start is not None,
            "next_start": next_start,
        }
    )


@router.get("/projects/{project_id}/inbox")
async def project_inbox(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    items, total = await ProjectNotificationService(db).inbox(
        project_id, target_handle=handle
    )
    return ok(page([_dump(n) for n in items], total))


@router.get("/projects/{project_id}/alerts/unread-count")
async def notifications_unread_count(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    """铃铛上的数字：这个人还没读、又不是 silent 的那些。算在服务端，省得客户端
    为了数个数把整份列表拉下来。"""
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    count = await ProjectNotificationService(db).unread_count(
        project_id, target_handle=handle
    )
    return ok({"unread": count})


@router.post("/projects/{project_id}/alerts/read-all")
async def mark_all_notifications_read(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
) -> dict:
    """全部标记已读（飞书那样）—— 只标调用者自己的，别人的一条不动。

    和上面那几条读不同，这里没有匿名那一档：它写 `read`，所以调用者必须拿着一个
    验证过的身份，光报一个名字要被拒。
    """
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id, allow_anonymous=False
    )
    marked = await ProjectNotificationService(db).mark_all_read(
        project_id, target_handle=handle
    )
    await db.commit()
    return ok({"marked": marked})


@router.post("/alerts/{notification_id}/read")
async def mark_notification_read(
    notification_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    service = ProjectNotificationService(db)
    row = await service.get_or_404(notification_id)
    await _acting_recipient(resolver, row)
    read = await service.mark_read(notification_id)
    await db.commit()
    return ok(_dump(read))


@router.post("/alerts/{notification_id}/resolve")
async def resolve_notification(
    notification_id: int,
    body: ResolveIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    """拍板一个决策请求 (spec G2)。决定记在验证过的调用者名下 —— body 里报来的
    名字一概不认。"""
    service = ProjectNotificationService(db)
    row = await service.get_or_404(notification_id)
    handle = await _acting_recipient(resolver, row)
    resolved = await service.resolve(
        notification_id,
        chosen=body.chosen,
        decided_by=handle,
        publish=get_broker().publish,
    )
    await db.commit()
    return ok(_dump(resolved))
