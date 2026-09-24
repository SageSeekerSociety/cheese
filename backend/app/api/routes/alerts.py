"""项目收件箱的路由 —— spec §8.5/8.6, evals G2/G3.

横跨两个资源前缀（项目下的集合 + 单条通知上的动作），所以这个 router 用空前缀，
每条路径写全。

每一条读写某个人信箱的端点都经 ``ActorResolver.resolve_recipient`` 认收件人 ——
那条规则住在信任边界上（app.api.auth），不在这里，所以下一个按收件人取信的端点
绕不过它。``target_handle`` 永远只是一句待核对的断言，不是身份本身。

路径上仍然叫 ``alerts``：URL 是对外的契约，而读写的表已经是 `notification` ——
平台报告自己的那些和人对人的那些同住一张收件箱（结论 58）。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ValidationError
from app.domain.notification.models import Notification
from app.domain.notification.schemas import (
    PROJECT_NOTIFICATION_KINDS,
    FeedbackIn,
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
    """往项目的收件箱里写 —— agent（带 scope 的令牌）、开发覆盖，或者一个登录了
    的人；匿名的进不来。路由自己核一遍凭据，而不是只靠中间件那道闸
    （见 ``require_verified_caller``）。

    返回的是**一组**：一条广播在这里就展开成一人一行，所以「刚写下的那一条」不再
    只有一个答案。
    """
    if body.kind not in PROJECT_NOTIFICATION_KINDS:
        raise ValidationError("这一类通知不从项目收件箱写入")
    await resolver.require_verified_caller(
        project_id=project_id, topic_id=body.topic_id
    )
    # `topic_id` 是 `topics` 的外键，而一条线程不是那张表里的行 —— 所以每个 agent
    # 手上那个地点 id（`$CHEESE_TOPIC`，对分身来说是线程的 id）违反约束，
    # `cheese_notify` 对它们全部 500。一条通知是发给人的，不是发给地点的，所以指
    # 向房间是诚实的做法。这是一次收窄：答复一个决策请求会把【决策】发回这个房
    # 间，于是线程里的问题答在它外面那个房间里。要带上线程得给它一列自己的
    # `task_id`，像块和用量已经有的那样。
    topic_id = body.topic_id
    if topic_id is not None:
        topic_id = (await TopicService(db).place_or_404(topic_id)).room_id
    rows = await ProjectNotificationService(db).create(
        project_id=project_id,
        level=body.level,
        kind=body.kind,
        title=body.title,
        body=body.body,
        target_handle=body.target_handle,
        topic_id=topic_id,
        payload=body.payload,
    )
    return ok(page([_dump(row) for row in rows], len(rows)))


@router.get("/projects/{project_id}/alerts")
async def list_notifications(
    project_id: uuid.UUID,
    db: DbSession,
    resolver: ActorResolverDep,
    target_handle: str | None = None,
    unread_only: bool = False,
) -> dict:
    """这个调用者在这个项目里的信。"""
    handle = await resolver.resolve_recipient(
        requested=target_handle, project_id=project_id
    )
    items, total = await ProjectNotificationService(db).list_for_project(
        project_id, target_handle=handle, unread_only=unread_only
    )
    return ok(page([_dump(n) for n in items], total))


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
    return ok({"marked": marked})


@router.post("/alerts/{notification_id}/read")
async def mark_notification_read(
    notification_id: int, db: DbSession, resolver: ActorResolverDep
) -> dict:
    service = ProjectNotificationService(db)
    row = await service.get_or_404(notification_id)
    await _acting_recipient(resolver, row)
    return ok(_dump(await service.mark_read(notification_id)))


@router.post("/alerts/{notification_id}/feedback")
async def set_notification_feedback(
    notification_id: int,
    body: FeedbackIn,
    db: DbSession,
    resolver: ActorResolverDep,
) -> dict:
    service = ProjectNotificationService(db)
    row = await service.get_or_404(notification_id)
    await _acting_recipient(resolver, row)
    return ok(_dump(await service.set_feedback(notification_id, body.feedback)))


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
    if row.resolved_at is None and await _carry_out(row, body.chosen, db, resolver):
        await db.commit()
        return ok(_dump(await service.get_or_404(notification_id)))
    resolved = await service.resolve(
        notification_id, chosen=body.chosen, decided_by=handle
    )
    return ok(_dump(resolved))


async def _carry_out(
    row: Notification, chosen: str, db: AsyncSession, resolver: ActorResolver
) -> bool:
    """A card that stands for a membership decision is answered by making it.

    Recording the choice alone would clear the card while the invitation or the
    join request stayed exactly where it was. The decision settles its own cards
    (every manager's, for a request), so there is nothing left to record here.
    Returns False for every other card.
    """
    from app.domain.membership.join_links import REQUEST_OPTIONS, JoinLinkService
    from app.domain.membership.services import INVITATION_OPTIONS, InvitationService

    payload = row.metadata_payload or {}
    request_id = payload.get("join_request_id")
    invitation_id = payload.get("invitation_id")
    if request_id is None and invitation_id is None:
        return False
    options = REQUEST_OPTIONS if request_id is not None else INVITATION_OPTIONS
    if chosen not in options or row.project_id is None:
        raise ValidationError("所选项不在候选项中")
    actor = await resolver.require_verified_caller()
    if request_id is not None:
        await JoinLinkService(db).decide(
            row.project_id,
            uuid.UUID(request_id),
            approve=chosen == options[0],
            actor=actor,
        )
    else:
        await InvitationService(db).respond(
            invitation_id=uuid.UUID(invitation_id),
            accept=chosen == options[0],
            actor=actor,
        )
    return True
