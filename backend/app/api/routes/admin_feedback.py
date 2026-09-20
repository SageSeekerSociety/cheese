"""管理端的反馈队列 —— 四个栏位的分诊台。

单独一个路由模块、单独一个前缀（`/admin/feedback`），和 `/feedback` 放在一起的
话，同一个模块里的两条路由会共享一个 `APIRouter`，而 `main.py` 的自动发现是
「一个模块一个 router」—— 第二个 router 会被静默丢掉（`_discover_routers` 用
`id(value)` 去重）。所以这是两个文件，不是一个文件里的两个 router。

进来先过 `require_admin`：平台管理员的判据在 `settings.feedback_admin_handles`
（理由见 `services.py` 顶部）。这里**没有**把某条反馈改成公开的入口 —— 公开与否是
提交者一次性的选择，管理员能改的话，就是唯一一个本人无法撤销的改动。
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import ActorResolver, ActorResolverDep
from app.api.response import ok, page
from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.domain.feedback import services as feedback_services
from app.domain.feedback.models import Feedback
from app.domain.feedback.schemas import (
    FeedbackCard,
    FeedbackPatch,
    FeedbackStatusIn,
    NoteCreate,
)

router = APIRouter(prefix="/admin/feedback", tags=["feedback"])

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def _require_admin(
    service: feedback_services.FeedbackService, resolver: ActorResolver
) -> str:
    """The two gates every handler in this module passes, in one place.

    `require_admin` answers 「是不是平台管理员」. The check above it answers
    「是不是人」, and they are separate questions: the allow-list is a list of
    handles, and an agent handle can be on it (`cheese` seats agents in rooms and
    a room's stand-in handle is derivable from the room). §4.3 requires the
    management surface to refuse an agent **explicitly, in the route body**,
    because `/admin/*` is not in `_CHEESE_WRITE_PATHS` — the middleware is a
    whitelist and this prefix is not on it, so nothing else would stop one.

    Reachable, not theoretical: a device screen's token (`X-Cheese-Screen`)
    resolves to its agent-as-user on any path, including one with no topic in it —
    unlike a per-turn `cheese` credential, which `ActorResolver` refuses when
    there is no project to scope it to.

    Agent first, then the allow-list, so the refusal an agent gets does not depend
    on whether it happens to be listed.
    """
    who = await resolver.resolve(fallback_handle=None)
    if who.is_agent:
        raise ForbiddenError("agent 不能执行管理动作")
    return await service.require_admin(who.handle if who.authenticated else None)


async def get_feedback_service(db: DbSession) -> feedback_services.FeedbackService:
    return feedback_services.FeedbackService(db)


FeedbackServiceDep = Annotated[
    feedback_services.FeedbackService, Depends(get_feedback_service)
]


async def _cards(
    service: feedback_services.FeedbackService,
    rows: list[Feedback],
    *,
    handle: str,
) -> list[dict]:
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
    service: feedback_services.FeedbackService, row: Feedback, *, handle: str
) -> dict:
    view = await service.detail_of(row, handle=handle, is_admin=True)
    return view.model_dump(mode="json")


@router.get("")
async def list_admin_feedback(
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
    tab: str = Query(default="public"),
    assignee: str | None = Query(default=None, max_length=64),
    q: str | None = Query(default=None, max_length=200),
    page_start: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict:
    """四个栏位：公开 / 私密 / agent 提的 / 安全。

    `tab` 不认识时报 400 而不是悄悄退回 `public` —— 管理端猜错栏位会让人以为
    「这条反馈不见了」，而它其实在隔壁那一栏。
    """
    handle = await _require_admin(service, resolver)
    rows, total = await service.list_admin(
        tab=tab, assignee=assignee, q=q, limit=page_size, offset=page_start
    )
    return ok(
        {
            **page(await _cards(service, rows, handle=handle), total),
            # is_admin=True: this is the one caller entitled to `unassigned`,
            # which counts the private and security queues too.
            "counts": await service.counts(handle=handle, is_admin=True),
        }
    )


@router.get("/{feedback_id}")
async def get_admin_feedback(
    feedback_id: uuid.UUID,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    handle = await _require_admin(service, resolver)
    row = await service.visible_row(feedback_id, handle=handle, is_admin=True)
    return ok(await _detail(service, row, handle=handle))


@router.patch("/{feedback_id}")
async def patch_admin_feedback(
    feedback_id: uuid.UUID,
    body: FeedbackPatch,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """改优先级 / 指派人 / 是否安全问题。**不接受 visibility，也不接受 status。**

    status 只走 `POST /{id}/status`，因为状态和它那条时间线必须在同一个事务里一起
    写 —— 从 PATCH 的字段里溜进去的话，就多了一条不写历史的路径。
    """
    handle = await _require_admin(service, resolver)
    row = await service.patch_admin(feedback_id, body, by_handle=handle)
    return ok(await _detail(service, row, handle=handle))


@router.post("/{feedback_id}/status")
async def set_admin_feedback_status(
    feedback_id: uuid.UUID,
    body: FeedbackStatusIn,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """推一个状态。和它那条时间线在同一个事务里落库（`services.set_status`）。

    同状态重复提交是空操作而不是第二条历史 —— 相隔一秒的两条一模一样的记录读起来
    像历史出了 bug。
    """
    handle = await _require_admin(service, resolver)
    row = await service.set_status(feedback_id, body.status, by_handle=handle)
    return ok(await _detail(service, row, handle=handle))


@router.post("/{feedback_id}/notes")
async def create_admin_feedback_note(
    feedback_id: uuid.UUID,
    body: NoteCreate,
    service: FeedbackServiceDep,
    resolver: ActorResolverDep,
) -> dict:
    """管理员之间的内部备注。只增不改。

    一个字符串列在两个管理员之间会互相覆盖，而「上一版写了什么」正是分诊时最需要
    知道的 —— 所以是行不是列（见 `FeedbackNote`）。
    """
    handle = await _require_admin(service, resolver)
    await service.note(feedback_id, body.body, author_handle=handle)
    row = await service.visible_row(feedback_id, handle=handle, is_admin=True)
    return ok(await _detail(service, row, handle=handle))
