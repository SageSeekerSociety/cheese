"""反馈中心 —— 平台级的收件箱，不属于任何项目。

为什么不在 `/topics/{id}/feedback` 下面：反馈是**平台**的功能。一条反馈可能来自
某个话题，也可能来自一个 harness 在沙箱里撞到的墙 —— 后者根本没有话题可挂。挂在
话题下面等于要求每条反馈先找到一个话题，而找不到话题恰恰是它要报的那类问题。

为什么用 `ActorResolver` 而不是 `require_auth_user`：这里的调用者有两族身份 ——
带 user id 的主干 token，和只有 handle 的 cheesex 会话 token（`user_id=None`，
在数字身份那一层被当成访客）。要 `require_auth_user` 就会把第二种挡在门外，而
harness 正是用第二种在敲门。`/awaiting-me` 是同一个先例。

访客能读公开列表，写操作要身份。私密条目的可见性并集（管理员 ∪ 提交者本人）在
`services.FeedbackService.may_see`，不在这一层 —— 路由拿不到判断权，就不会漏。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import AuthenticationRequiredError, BadRequestError
from app.domain.feedback import services as feedback_services
from app.domain.feedback.models import (
    Feedback,
    FeedbackKind,
    FeedbackPriority,
    FeedbackStatus,
    FeedbackVisibility,
)
from app.domain.feedback.schemas import (
    CommentCreate,
    CommentOut,
    FeedbackCard,
    FeedbackCounts,
    FeedbackCreate,
    FeedbackMeta,
    SupportOut,
)

router = APIRouter(prefix="/feedback", tags=["feedback"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_feedback_service(db: DbSession) -> feedback_services.FeedbackService:
    return feedback_services.FeedbackService(db)


FeedbackServiceDep = Annotated[
    feedback_services.FeedbackService, Depends(get_feedback_service)
]


async def _cards(
    service: feedback_services.FeedbackService,
    rows: list[Feedback],
    *,
    handle: str | None,
) -> list[dict]:
    """Turn a page of rows into JSON.

    Four queries for the whole page — support counts, comment counts, who
    already supported, last activity — instead of four per row. The `hot` tab is
    *sorted by* the number a per-row fetch would be re-reading twenty times.
    """
    ids = [r.id for r in rows]
    supports = await service.support_counts(ids)
    comments = await service.comment_counts(ids)
    supported = await service.supported_ids(ids, handle)
    activity = await service.last_activity(ids)
    return [
        FeedbackCard.from_row(
            row,
            supports=supports.get(row.id, 0),
            comments=comments.get(row.id, 0),
            supported=row.id in supported,
            last_activity_at=activity.get(row.id),
        ).model_dump(mode="json")
        for row in rows
    ]


async def _detail(
    service: feedback_services.FeedbackService,
    row: Feedback,
    *,
    handle: str | None,
    is_admin: bool,
) -> dict:
    view = await service.detail_of(row, handle=handle, is_admin=is_admin)
    return view.model_dump(mode="json")


def _is_admin(service: feedback_services.FeedbackService, handle: str | None) -> bool:
    return service.is_admin(handle)


@router.get("/meta")
async def get_feedback_meta(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """词表。客户端不再硬编码有哪些状态、按什么顺序流动。

    颜色留在前端（那是视觉决定），**有哪些取值**从前端搬到这里 —— 因为加一个
    状态是后端改一处的事，而前端的常量表要靠发版才能跟上。
    """
    who = await resolver.resolve(fallback_handle=None)
    meta = FeedbackMeta(
        kinds=list(FeedbackKind),
        statuses=list(FeedbackStatus),
        priorities=list(FeedbackPriority),
        visibilities=list(FeedbackVisibility),
        status_ladder=list(feedback_services.STATUS_LADDER),
        tabs=list(feedback_services.PUBLIC_TABS),
        admin_tabs=list(feedback_services.ADMIN_TABS),
        hot_supports=feedback_services.repo.HOT_SUPPORTS,
        is_admin=_is_admin(service, who.handle if who.authenticated else None),
    )
    return ok(meta.model_dump(mode="json"))


@router.get("/counts")
async def get_feedback_counts(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """铃铛和标签页上的数字，单独一个入口。

    列表接口也带 counts，但铃铛要在没打开反馈中心的时候轮询，不该为了一个整数
    拉一整页数据。
    """
    who = await resolver.resolve(fallback_handle=None)
    handle = who.handle if who.authenticated else None
    # No `unassigned` here: it is the admin queue's 「还没人管」, counted over the
    # private and security rows as well, and this endpoint answers anonymous
    # callers (the bell polls it before anyone logs in). An admin gets it from
    # `GET /admin/feedback`. See `FeedbackService.counts`.
    counts = await service.counts(handle=handle)
    payload = FeedbackCounts(
        all=counts["all"],
        hot=counts["hot"],
        active=counts["active"],
        resolved=counts["resolved"],
        unread=counts["unread"],
    )
    return ok(payload.model_dump(mode="json"))


@router.post("/read")
async def mark_feedback_read(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """把未读游标推到此刻。

    游标而不是每条一个「已读」行：见 `FeedbackReadState`。返回游标值，客户端可以
    拿它做后续请求的下界，省掉一次「现在几点」的猜测。
    """
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError("需要登录")
    at = await service.mark_read(handle=who.handle)
    return ok({"last_read_at": at.isoformat()})


@router.get("/mine")
async def list_my_feedback(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    page_start: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """「我的反馈」：我提的 + 我替谁提的 + 指派给我的。

    访客拿到空列表而不是 401 —— 和 `/awaiting-me` 同一条理由：这个清单的定义是
    「点到我的那些」，没有身份就没有被点到。
    """
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        return ok(page([], 0))
    rows, total = await service.list_mine(
        handle=who.handle, limit=page_size, offset=page_start
    )
    items = await _cards(service, rows, handle=who.handle)
    return ok({**page(items, total), "counts": await service.counts(handle=who.handle)})


@router.get("")
async def list_feedback(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    tab: str = Query(default="all"),
    q: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="new"),
    page_start: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    who = await resolver.resolve(fallback_handle=None)
    handle = who.handle if who.authenticated else None
    if tab not in feedback_services.PUBLIC_TABS:
        # Same refusal as the admin list: a tab the server does not know is a
        # client that has fallen behind, and answering with `all` renders a
        # plausible page under the wrong heading. A user cannot type a tab name,
        # so nobody reaches this by hand.
        raise BadRequestError(f"未知的视图：{tab}")
    rows, total = await service.list_public(
        tab=tab, q=q, sort=sort, limit=page_size, offset=page_start
    )
    items = await _cards(service, rows, handle=handle)
    return ok({**page(items, total), "counts": await service.counts(handle=handle)})


@router.post("")
async def create_feedback(
    body: FeedbackCreate,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """提一条反馈。

    `visibility` 是提交者的选择，只在这一次决定：私密条目只有管理员和提交者本人
    看得见（并集在 `services.may_see`），而管理员**没有**把它改公开的入口 ——
    那是唯一一个本人无法撤销的改动，所以不给。

    agent **不能**走这条路：`services.create` 会拒（§5.2）。agent 的入口是
    `cheese feedback propose`，它落的是一张提案卡，由人在卡上按发送 ——
    没有这道门，一个跑歪的循环可以直接往公开列表里灌东西。人在卡上按发送时走的是
    `POST /topics/{topic_id}/feedback-proposals/{block_id}/accept`，那条路会把作者
    记成提案的 agent、把提交者记成按按钮的人。
    """
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError("需要登录")
    row = await service.create(
        body,
        actor_handle=who.handle,
        actor_user_id=who.user_id,
        actor_is_agent=who.is_agent,
    )
    return ok(
        await _detail(
            service, row, handle=who.handle, is_admin=service.is_admin(who.handle)
        )
    )


@router.get("/{feedback_id}")
async def get_feedback(
    feedback_id: uuid.UUID,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """一条反馈的全部内容。看不见的 id 回 404，不是 403 —— 见 `visible_row`。"""
    who = await resolver.resolve(fallback_handle=None)
    handle = who.handle if who.authenticated else None
    row = await service.visible_row(
        feedback_id, handle=handle, is_admin=service.is_admin(handle)
    )
    return ok(
        await _detail(service, row, handle=handle, is_admin=service.is_admin(handle))
    )


@router.get("/{feedback_id}/comments")
async def list_feedback_comments(
    feedback_id: uuid.UUID,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None)
    handle = who.handle if who.authenticated else None
    row = await service.visible_row(
        feedback_id, handle=handle, is_admin=service.is_admin(handle)
    )
    thread = await service.thread(row.id)
    return ok([CommentOut.model_validate(c).model_dump(mode="json") for c in thread])


@router.post("/{feedback_id}/comments")
async def create_feedback_comment(
    feedback_id: uuid.UUID,
    body: CommentCreate,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError("需要登录")
    comment, _ = await service.comment(
        feedback_id,
        body.body,
        body.parent_id,
        actor_handle=who.handle,
        actor_user_id=who.user_id,
        actor_is_agent=who.is_agent,
        is_admin=service.is_admin(who.handle),
    )
    return ok(CommentOut.model_validate(comment).model_dump(mode="json"))


@router.delete("/{feedback_id}/comments/{comment_id}")
async def delete_feedback_comment(
    feedback_id: uuid.UUID,
    comment_id: uuid.UUID,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError("需要登录")
    await service.delete_comment(
        feedback_id,
        comment_id,
        handle=who.handle,
        is_admin=service.is_admin(who.handle),
    )
    return ok({"deleted": True})


@router.post("/{feedback_id}/supports")
async def support_feedback(
    feedback_id: uuid.UUID,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """支持一条。重复点是幂等的，回的是**写完之后**的计数而不是增量。

    返回增量的话，两个人同时点会各自渲染出一个从来没存在过的数字。
    """
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError("需要登录")
    count, supported = await service.support(
        feedback_id, handle=who.handle, is_admin=service.is_admin(who.handle)
    )
    return ok(SupportOut(count=count, supported=supported).model_dump(mode="json"))


@router.delete("/{feedback_id}/supports")
async def unsupport_feedback(
    feedback_id: uuid.UUID,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    who = await resolver.resolve(fallback_handle=None)
    if not who.authenticated or not who.handle:
        raise AuthenticationRequiredError("需要登录")
    count, supported = await service.unsupport(
        feedback_id, handle=who.handle, is_admin=service.is_admin(who.handle)
    )
    return ok(SupportOut(count=count, supported=supported).model_dump(mode="json"))
