"""提交与批阅。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.routes.tasks._common import (
    CreateSubmissionReviewRequest,
    PatchSubmissionReviewRequest,
    get_task_membership_service,
    get_task_service,
    get_task_submission_review_service,
    get_task_submission_service,
    get_team_service,
)

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.space_access import may_teach_task
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
)
from app.core.sentences import say
from app.db.session import get_db
from app.domain.task.access import (
    bind_review_path,
    ensure_can_read_participation,
)
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionReviewService,
    TaskSubmissionService,
)
from app.domain.task.submission_state import past_deadline
from app.domain.team.services import TeamService

router = APIRouter(prefix="/tasks")


@router.get(
    "/{taskId}/participants/{participantId}/submissions",
    summary="List Task Submissions",
)
async def get_task_submissions(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    allVersions: bool = Query(default=False),
    queryReview: bool = Query(default=False),
    reviewed: bool | None = Query(default=None),
    pageStart: int | None = Query(default=None, ge=0),
    pageSize: int = Query(default=20, ge=1, le=100),
    sortBy: str = Query(default="updatedAt"),
    sortOrder: str = Query(default="desc"),
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Enumerate submissions for a given task participant."""
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    # 出题者或本版管理员看得到这道题下任何人的提交；成员只看自己（或自己
    # 所在小队）的那一份。
    await ensure_can_read_participation(
        session=db,
        task=task,
        membership=membership,
        user_id=auth_user.user_id,
        team_service=team_service,
        forbidden_message="You are not authorized to view these submissions",
    )

    if sortBy not in {"createdAt", "updatedAt"}:
        raise BadRequestError(f"Invalid sortBy: {sortBy}")
    if sortOrder not in {"asc", "desc"}:
        raise BadRequestError(f"Invalid sortOrder: {sortOrder}")

    offset = pageStart or 0
    if offset < 0:
        offset = 0

    items, total = await submission_service.list_submissions(
        task_id=task_id,
        participant_id=participant_id,
        all_versions=allVersions,
        query_review=queryReview,
        reviewed=reviewed,
        limit=pageSize,
        offset=offset,
        sort_by=sortBy,
        sort_order=sortOrder,
    )
    returned = len(items)
    has_more = offset + returned < total
    next_start = offset + returned if has_more and returned > 0 else None

    page = {
        "pageStart": offset,
        "pageSize": returned,
        "hasMore": has_more,
        "nextStart": next_start,
        "total": total,
    }

    return {
        "code": 200,
        "message": "OK",
        "data": {"submissions": items, "page": page},
    }


@router.post(
    "/{taskId}/participants/{participantId}/submissions",
    summary="Create Submission",
)
async def post_task_submission(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    contents: list[dict],
    db=Depends(get_db),
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    if membership.approved != 0:
        raise ForbiddenError("Participant must be approved before submitting")
    if task.ended_at is not None:
        raise BadRequestError("Cannot submit to an ended task")
    # A claim's own deadline (set when it is approved, moved by whoever teaches
    # the task) is the last moment to hand work in: past it with nothing in hand
    # the claim reads FAILED (`submission_state`, and the deadline sweep writes
    # the same). A version taken after it would turn that FAILED back into
    # PENDING_REVIEW behind the teacher's back; moving the deadline is how a
    # late hand-in is let through.
    if past_deadline(membership.deadline, datetime.now(UTC)):
        raise BadRequestError(say("submissionPastDeadline"))

    if membership.is_team:
        is_member = await team_service.is_team_member(
            membership.member_id, auth_user.user_id
        )
        if not is_member:
            raise ForbiddenError("Only team members can submit for this team task")
    else:
        if membership.member_id != auth_user.user_id:
            raise ForbiddenError("Only the participant themselves can submit")

    if not task.resubmittable:
        existing, _ = await submission_service.list_submissions(
            task_id=task_id,
            participant_id=participant_id,
            all_versions=False,
            query_review=False,
            limit=1,
        )
        if existing:
            raise BadRequestError("Task does not allow resubmission")

    submission_dto = await submission_service.submit_task(
        task_id=task_id,
        participant_id=participant_id,
        submitter_id=auth_user.user_id,
        contents=contents,
    )
    # Commit before answering: the page that just submitted opens the submission
    # history next, and must find what it handed in. Same reason as the commit in
    # ``create_task``.
    await db.commit()
    return {
        "code": 200,
        "message": "OK",
        "data": {"submission": submission_dto},
    }


@router.patch(
    "/{taskId}/participants/{participantId}/submissions/{version}",
    summary="Update Submission",
)
async def patch_task_submission(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    version: Annotated[int, Path(ge=0)],
    contents: list[dict],
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    if not task.editable:
        raise BadRequestError("Task does not allow editing submissions")

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    # Editing a version hands work in just as a new version does, so it takes the
    # same door as ``post_task_submission``: an approved claim, a challenge still
    # running, and not past the claim's own deadline.
    if membership.approved != 0:
        raise ForbiddenError("Participant must be approved before submitting")
    if task.ended_at is not None:
        raise BadRequestError("Cannot submit to an ended task")
    if past_deadline(membership.deadline, datetime.now(UTC)):
        raise BadRequestError(say("submissionPastDeadline"))

    if membership.is_team:
        is_member = await team_service.is_team_member(
            membership.member_id, auth_user.user_id
        )
        if not is_member:
            raise ForbiddenError("Only team members can edit this submission")
    else:
        if membership.member_id != auth_user.user_id:
            raise ForbiddenError(
                "Only the participant themselves can edit their submission"
            )

    submission_dto = await submission_service.modify_submission(
        task_id=task_id,
        participant_id=participant_id,
        submitter_id=auth_user.user_id,
        version=version,
        contents=contents,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"submission": submission_dto},
    }


@router.post(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Create Submission Review",
)
async def post_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: CreateSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can create review")

    existing = await review_service.get_review_dto(submission_id)
    if existing.get("reviewed"):
        raise ConflictError("Review already exists for this submission")

    review_dto = await review_service.create_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.get(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Get Submission Review",
)
async def get_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    team_service: TeamService = Depends(get_team_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """一条评审的读者，就是这条提交的读者。

    以前这里整个函数没有鉴权（只把三个参数 ``_ = (...)`` 丢掉），登录的人知道一个
    submissionId 就能读到它的成绩与评语。判据照抄同一路径上的提交列表
    ``GET .../submissions``：出题者与管理员、提交者本人、以及小队提交时的小队成员。
    """
    task, membership = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )

    await ensure_can_read_participation(
        session=db,
        task=task,
        membership=membership,
        user_id=auth_user.user_id,
        team_service=team_service,
        forbidden_message="You are not authorized to view this review",
    )

    review_dto = await review_service.get_review_dto(submission_id)

    if not review_dto.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.patch(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Re-Review Submission",
)
async def patch_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: PatchSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can update review")

    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.put(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Update Submission Review (Full Replace)",
)
async def put_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    payload: CreateSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can update review")

    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
    )
    return {
        "code": 200,
        "message": "OK",
        "data": {"review": review_dto},
    }


@router.delete(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Delete Submission Review",
)
async def delete_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    task, _ = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    if not await may_teach_task(session=db, task=task, user_id=auth_user.user_id):
        raise ForbiddenError("Only the author or a board manager can delete review")

    existing = await review_service.get_review_dto(submission_id)
    if not existing.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    await review_service.delete_review(submission_id=submission_id)
    review_dto = await review_service.get_review_dto(submission_id)
    return {"code": 200, "message": "OK", "data": {"review": review_dto}}
