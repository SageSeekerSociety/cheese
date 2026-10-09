"""The space analytics board: one space's work, read back as numbers.

A slice of `app/api/routes/spaces.py` (arch review, tracking issue #2143): the
tail of the file -- the whole "New Analytics Endpoints (NT-API aligned)"
section. The overview, alert, publisher, participant and people cards, the
participant CSV export and the submission queue (physically inside this
section) all read one space's tasks, submissions and roster; the four learning
routes read the conversations inside its members' projects, and the tasks and
publishers exports close the set. That is one concept -- an administrator
looking at a board -- which is why they leave together.

The gate is the administrator board `_ensure_space_admin` everywhere except the
learning four, which `app.auth.project_access` judges per project (its own
comment above those routes says why the split is real and not an oversight).

It is a *suffix* of that file's route list, and the module sorts exactly where
the block stood (`spaces.py` < `spaces_analytics.py` < `spaces_member.py` <
`spaces_organization.py`), so moving it leaves every path, method,
`operationId` and their order exactly where they were, and the module mounts
itself through `app.main._discover_routers` like every other route module.

The helpers it shares with the routes that stay -- `_ensure_space_admin`,
`_ensure_space_visible`, `get_space_analytics_view_service`,
`get_space_user_realname_service`, `require_reviewed_space` and the file's
`_logger` -- stay in `spaces.py` and are imported here, the shape
`spaces_member.py` and `spaces_organization.py` already use. `_logger` comes
along so the one audit warning the participant export writes keeps its channel
(`app.api.routes.spaces`); `get_space_user_realname_service` stays behind even
though nothing else in `spaces.py` now calls it, next to the other
`get_space_*_service` factories. `spaces.py` imports nothing from this module,
so there is no cycle.

Nothing here reads an `app.domain.*.models` module -- the five services are
reached through `app.domain.space.analytics_view_service`,
`app.domain.space.learning_service`, `app.domain.task.services` and
`app.domain.user.realname_services` -- so `.importlinter`'s C2
(`routes-touch-no-models`) is untouched and the frozen baseline does not move.
`get_space_learning_service`, `get_space_submission_service`, `_learning_handle`
and the one request body `LearningOutlineRequest` are named nowhere else, so
they move with the routes that use them.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import ActorResolverDep
from app.api.routes.spaces import (
    _ensure_space_admin,
    _ensure_space_visible,
    _logger,
    get_space_analytics_view_service,
    get_space_user_realname_service,
    require_reviewed_space,
)
from app.api.routes.tasks._common import get_task_submission_service
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.core.client_address import resolved_client_address
from app.core.errors import BadRequestError, NotFoundError
from app.db.session import get_db
from app.domain.space.analytics_view_service import SpaceAnalyticsViewService
from app.domain.space.learning_service import SpaceLearningService
from app.domain.task.services import TaskSubmissionService
from app.domain.user.realname_services import UserRealNameService

router = APIRouter(
    prefix="/spaces", tags=["Spaces"], dependencies=[Depends(require_reviewed_space)]
)


# ---------------------------------------------------------------------------
# New Analytics Endpoints (NT-API aligned)
# ---------------------------------------------------------------------------


@router.get(
    "/{spaceId}/analytics/overview",
    summary="Get Space Analytics Overview",
)
async def get_space_analytics_overview(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    groupBy: str = Query(default="day"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> dict:
    """Return KPI cards, trend data, and distribution summaries for the space."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    data = await service.get_overview(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        group_by=groupBy,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/alerts",
    summary="Get Space Analytics Alerts",
)
async def get_space_analytics_alerts(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> dict:
    """Return governance alert cards for the space."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    data = await service.get_alerts(space_id=space_id)
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/publishers",
    summary="Get Space Analytics Publishers",
)
async def get_space_analytics_publishers(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    sortBy: str = Query(default="taskCount"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> dict:
    """Return publisher comparison table data."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    data = await service.get_publishers(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        task_approved=taskApproved,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/participants",
    summary="Get Space Analytics Participants",
)
async def get_space_analytics_participants(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    participationApproved: str | None = Query(default=None),
    completionStatus: str | None = Query(default=None),
    realName: str = Query(default="all"),
    groupBy: str = Query(default="day"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> dict:
    """Return participant population and completion analytics."""
    # 这一格把 ``_decode_identity`` 出来的年级/专业/班级做成分组统计 —— 是成员
    # 个人信息的聚合，所以和下面的导出同一个门：管理员版面只有管理员看。非管理员答
    # 403（不是空的分布），原因和导出一样：空的会把「你没权限」说成「这个班没人」。
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    data = await service.get_participants(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        participation_approved=participationApproved,
        completion_status=completionStatus,
        real_name=realName,
        group_by=groupBy,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/people",
    summary="Get Space Analytics People",
)
async def get_space_analytics_people(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> dict:
    """Return per-person participation rows plus the claims that never moved."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    data = await service.get_people(space_id=space_id)
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/participants/export",
    summary="Export Space Analytics Participants",
)
async def export_space_analytics_participants(
    request: Request,
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    participationApproved: str | None = Query(default=None),
    completionStatus: str | None = Query(default=None),
    realName: str = Query(default="all"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    realname_service: UserRealNameService = Depends(get_space_user_realname_service),
    db=Depends(get_db),
) -> Response:
    """Export participant analytics as CSV (22 columns, NT-aligned).

    Writes one `UserRealNameAccessLog` row per distinct personal (non-team)
    target user to audit real-name data access, matching NT's
    `auditSpaceParticipantExport` behavior.
    """
    # 管理员版面：这份 CSV 逐行写着成员的真实姓名、学号、年级、专业、班级、电话、
    # 邮箱（``_decode_identity`` 负责解密），所以只有题目板的管理员/创建者能拿。
    # 非管理员明确 403 —— 不返回空 CSV，空的会把「你没权限」误报成「这个班没人」。
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    csv_text, memberships = await service.export_participants_csv(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        participation_approved=participationApproved,
        completion_status=completionStatus,
        real_name=realName,
    )

    # Audit: per-target user real-name access log (dedup by member_id).
    access_reason = (
        "Export space analytics participants with filters: "
        f"from={from_ts}, to={to_ts}, categoryId={categoryId}, "
        f"publisherId={publisherId}, taskApproved={taskApproved}, "
        f"participationApproved={participationApproved}, "
        f"completionStatus={completionStatus}, realName={realName}"
    )
    ip_address = resolved_client_address(request) or ""
    seen_target_ids: set[int] = set()
    for m in memberships:
        if m.is_team:
            continue
        if m.member_id in seen_target_ids:
            continue
        seen_target_ids.add(m.member_id)
        try:
            await realname_service.log_access(
                accessor_id=auth_user.user_id,
                target_id=m.member_id,
                access_reason=access_reason,
                access_type="EXPORT",
                ip_address=ip_address,
                module_type="SPACE",
                module_entity_id=space_id,
            )
        except NotFoundError:
            # Target user may have been soft-deleted; skip audit but continue export.
            _logger.warning(
                "Skip participant export audit: user not found",
                extra={"space_id": space_id, "target_id": m.member_id},
            )

    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=space-{space_id}-participants.csv"  # noqa: E501
        },
    )


# ── 学习: 成员怎么与 AI 协作、卡在哪 (issue #945 的管理员看板) ────────────────────
#
# 上面那一组读 赛题 与报名表，这一组读成员项目里的**对话**，所以门也不同: 课程页
# 本身对所有人可见（`Role.GUEST` 就能读 Space），成员项目的对话不是。行数据的判定
# 不写在这几条路由里 —— 它在 `app.auth.project_access`，由 `SpaceLearningService`
# 逐个项目过一次（`ActorResolver.authorize_project` 是同一个判据的请求内形态）。
# 这两条只负责「先登录」，和本文件其它路由同一个写法。
#
# 唯一的例外是 `filters`：它除了行数据（成员、计数）还报课程级的分类名，那一项没有
# 项目可逐条过，所以那条路由照本文件的空间路由挂了 `_ensure_space_visible` —— 见它
# 自己的说明。
#
# 缺了哪些数据（review_flag、「再给一点提示」、知识点）写在 `SpaceLearningService`
# 的模块说明里，接口如实把它们报成缺失，不拿别的信号顶替。


class LearningOutlineRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    block_ids: list[uuid.UUID] = Field(default_factory=list, alias="blockIds")


async def get_space_submission_service(
    db=Depends(get_db),
) -> TaskSubmissionService:
    """空间提交队列要的提交服务: 走 `routes.tasks` 那个现成的装配点。"""

    return await get_task_submission_service(db=db)


@router.get(
    "/{spaceId}/submissions",
    summary="Get Space Submission Queue",
)
async def get_space_submissions(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    reviewed: bool | None = Query(default=None),
    taskId: int | None = Query(default=None),
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=200),
    sortBy: str = Query(default="createdAt"),
    sortOrder: str = Query(default="desc"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    submission_service: TaskSubmissionService = Depends(get_space_submission_service),
    db=Depends(get_db),
) -> dict:
    """一整门课的提交与验收队列 —— 管理员看的那一屏。

    按板子取一次，而不是逐道题 × 逐个成员地问（那是 N×M 次请求）。每行都带
    `taskId` / `taskTitle` / `participantId`，管理员看的是「谁的哪份作业」。

    判据走那道现成的管理员闸 `_ensure_space_admin`：不在这个板里答 404（不确认它
    存在），在板里但不是管理员答 403。成员看自己那一份走既有的按题接口 ——
    整门课的提交是管理员版面。`reviewed=false` 就是验收队列；不给就是全部。
    """
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")

    offset = max(pageStart or 0, 0)
    items, total = await submission_service.list_for_space(
        space_id=space_id,
        task_id=taskId,
        reviewed=reviewed,
        limit=pageSize,
        offset=offset,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    returned = len(items)
    has_more = offset + returned < total
    summary = await submission_service.summary_for_space(
        space_id=space_id,
        task_id=taskId,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {
            "submissions": items,
            "summary": summary,
            "page": {
                "pageStart": offset,
                "pageSize": returned,
                "hasMore": has_more,
                "nextStart": offset + returned if has_more and returned > 0 else None,
                "total": total,
            },
        },
    }


async def get_space_learning_service(db=Depends(get_db)) -> SpaceLearningService:
    return SpaceLearningService(session=db)


def _learning_handle(actor) -> str | None:
    """读课程对话用的是谁的 handle。

    未登录给 None —— `may_read_project` 对 None 一律回 False（"nobody asked" 不
    能读成 "anybody may"），于是页面是空的，而不是全的。
    """
    return actor.handle if actor.authenticated else None


@router.get(
    "/{spaceId}/analytics/learning/filters",
    summary="Get Space Learning Filters",
)
async def get_space_learning_filters(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    resolver: ActorResolverDep,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
    db=Depends(get_db),
) -> dict:
    """这一格能筛的两维: 成员、知识点。时间那一维在前端的筛选栏里。

    这里比同族其它三条多一道 `_ensure_space_visible`，因为返回的东西里有一项不
    是行数据: `knowledgePoints` 报的是这个课程自己划的分类格子
    (`space_categories`)，不是某个成员项目里的行。成员与计数走
    `SpaceLearningService` 那道逐项目的 `may_read_project`，一个都读不到就是空
    表；分类名没有项目可逐条过，只有课程级的一道门能挡 —— 少了它，一个不在这个
    板里的人在 404 的 `GET /spaces/{id}` 旁边拿到 200，还能读出别人课程的设计。
    门本身照抄本文件其它空间路由的那道 (非成员答 404，不确认板子存在)。
    """
    await _ensure_space_visible(db=db, space_id=space_id, user_id=auth_user.user_id)
    actor = await resolver.resolve()
    data = await service.filters(space_id=space_id, handle=_learning_handle(actor))
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/learning/questions",
    summary="Get Space Learning Questions",
)
async def get_space_learning_questions(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    resolver: ActorResolverDep,
    student: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    knowledgePoint: int | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """按成员 / 时间 / 知识点筛出来的成员发言，每条都带得回原文的坐标。"""
    _ = auth_user
    actor = await resolver.resolve()
    data = await service.questions(
        space_id=space_id,
        handle=_learning_handle(actor),
        student=student,
        from_ts=from_ts,
        to_ts=to_ts,
        knowledge_point=knowledgePoint,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/learning/queues",
    summary="Get Space Learning Queues",
)
async def get_space_learning_queues(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    resolver: ActorResolverDep,
    student: str | None = Query(default=None),
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """共性问题两条来源，各自一个队列。"""
    _ = auth_user
    actor = await resolver.resolve()
    data = await service.queues(
        space_id=space_id,
        handle=_learning_handle(actor),
        student=student,
        from_ts=from_ts,
        to_ts=to_ts,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.post(
    "/{spaceId}/analytics/learning/outline",
    summary="Build Space Learning Outline",
)
async def build_space_learning_outline(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    body: LearningOutlineRequest,
    resolver: ActorResolverDep,
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceLearningService = Depends(get_space_learning_service),
) -> dict:
    """把勾中的几条拼成一份能直接上课用的讲解提纲。

    POST 而不是 GET: 勾的是哪几条会随人一直变，而且可能几十个 id —— 放进查询串
    会撞上长度上限，也会在访问日志里留下别人的引用。
    """
    _ = auth_user
    actor = await resolver.resolve()
    data = await service.outline(
        space_id=space_id,
        handle=_learning_handle(actor),
        block_ids=body.block_ids,
    )
    return {"code": 200, "message": "OK", "data": data}


@router.get(
    "/{spaceId}/analytics/tasks/export",
    summary="Export Space Analytics Tasks",
)
async def export_space_analytics_tasks(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    publisherId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    hasPendingReview: bool | None = Query(default=None),
    hasPendingApproval: bool | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> Response:
    """Export task analytics as CSV (16 columns, NT-aligned)."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    csv_text = await service.export_tasks_csv(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        publisher_id=publisherId,
        task_approved=taskApproved,
        has_pending_review=hasPendingReview,
        has_pending_approval=hasPendingApproval,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="space-{space_id}-tasks.csv"'
        },
    )


@router.get(
    "/{spaceId}/analytics/publishers/export",
    summary="Export Space Analytics Publishers",
)
async def export_space_analytics_publishers(
    space_id: Annotated[int, Path(ge=1, alias="spaceId")],
    from_ts: int | None = Query(default=None, alias="from"),
    to_ts: int | None = Query(default=None, alias="to"),
    categoryId: int | None = Query(default=None),
    taskApproved: str | None = Query(default=None),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    service: SpaceAnalyticsViewService = Depends(get_space_analytics_view_service),
    db=Depends(get_db),
) -> Response:
    """Export publisher analytics as CSV (11 columns, NT-aligned)."""
    await _ensure_space_admin(db=db, space_id=space_id, user_id=auth_user.user_id)
    _ = auth_user
    csv_text = await service.export_publishers_csv(
        space_id=space_id,
        from_ts=from_ts,
        to_ts=to_ts,
        category_id=categoryId,
        task_approved=taskApproved,
    )
    return Response(
        content=csv_text,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="space-{space_id}-publishers.csv"'  # noqa: E501
        },
    )
