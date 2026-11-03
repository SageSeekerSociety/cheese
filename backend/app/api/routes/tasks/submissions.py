"""提交与批阅。"""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel

from app.api.response import ok, typed_response
from app.api.routes.tasks._common import (
    CreateSubmissionReviewRequest,
    PatchSubmissionReviewRequest,
)
from app.api.task_deps import (
    get_task_membership_service,
    get_task_service,
    get_task_submission_review_service,
    get_task_submission_service,
)
from app.api.write_access import Can
from app.auth import can
from app.auth import task_roles as task_seats

# 给了题目的「给 AI 队友的指导」(#944)：请求体沿用 项目集 PATCH 那个严格模型，
# 读写与引用校验在 app.api.task_teaching 里，接口形状在 app.api.task_serialization。
from app.auth.checker import require_auth_user
from app.auth.core import AuthUserInfo
from app.auth.task_roles import ClaimPermission
from app.auth.task_roles import Permission as TaskPermission
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
)
from app.domain.task.claims import claim_of
from app.domain.task.services import (
    TaskMembershipService,
    TaskService,
    TaskSubmissionReviewService,
    TaskSubmissionService,
)
from app.domain.task.stages import (
    open_stage,
    stage_index,
    stage_lines,
    stages_to_api,
)
from app.domain.task.submission_state import (
    past_deadline,
    stage_change_effect,
    stage_distribution,
    stage_states_of,
)
from app.domain.task_notices import notices as task_notices

router = APIRouter(prefix="/tasks")


class TaskStageOut(BaseModel):
    name: str
    description: str
    dueAt: int | None


class MyStageProgress(BaseModel):
    participantId: int
    stages: list[str]


class TaskProgress(BaseModel):
    stages: list[TaskStageOut]
    mine: MyStageProgress | None
    distribution: list[dict[str, int]] | None


@router.get(
    "/{taskId}/progress",
    summary="Task Progress By Stage",
    **typed_response(TaskProgress),
    dependencies=[Can(TaskPermission.view)],
)
async def get_task_progress(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """Where things stand on each of the task's stages.

    ``mine``: the caller's own claim (approved), one status per stage
    (``submission_state`` STAGE_*), or null without one. ``distribution``: for
    whoever grades the task, how many approved claims are at each status on
    each stage; null for everyone else. A task without stages has one stage.
    """
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    claim = await claim_of(db, task=task, user_id=auth_user.user_id)
    mine = (
        {
            "participantId": claim.id,
            "stages": await stage_states_of(db, claim),
        }
        if claim is not None and claim.approved == 0
        else None
    )
    distribution = (
        await stage_distribution(db, task.id)
        if await task_seats.teaches(db, task, auth_user.user_id)
        else None
    )
    return ok(
        {
            "stages": stages_to_api(await task_service.list_stages(task.id)),
            "mine": mine,
            "distribution": distribution,
        }
    )


class StageChangeIn(BaseModel):
    #: The stages as the save would send them (``PATCH /tasks/{id}``'s ``stages``).
    stages: list[dict]


class StageChangeEffectOut(BaseModel):
    failing: int
    reopening: int
    #: Who those are, by name (a team's claim by the team's name), in claim order.
    failingNames: list[str]
    reopeningNames: list[str]
    #: The stages, by name, that would be past due with nothing handed in.
    failingStages: list[str]


@router.post(
    "/{taskId}/stages/effect",
    summary="What Saving These Stages Would Do To Claims",
    dependencies=[Can(TaskPermission.teach)],
    **typed_response(StageChangeEffectOut),
)
async def get_stage_change_effect(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    body: StageChangeIn,
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
) -> dict:
    """How many claims saving ``stages`` would turn failed (``failing``) and
    how many it would take out of failed again (``reopening``), who they are
    and which stages they would miss, so the publisher can be told before
    saving. Nothing is written. Only whoever may
    change the task asks."""
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)
    effect = await stage_change_effect(db, task.id, stage_lines(body.stages))
    return ok(
        {
            "failing": effect.failing,
            "reopening": effect.reopening,
            "failingNames": list(effect.failing_names),
            "reopeningNames": list(effect.reopening_names),
            "failingStages": list(effect.failing_stages),
        }
    )


@router.get(
    "/{taskId}/participants/{participantId}/submissions",
    summary="List Task Submissions",
    dependencies=[Can(TaskPermission.view)],
)
async def get_task_submissions(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    stage: str | None = Query(default=None, max_length=16),
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
    await can.require(db, auth_user.user_id, ClaimPermission.read, membership)

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
        stage=(
            stage_index(stage, await task_service.stage_keys(task_id))
            if stage is not None
            else None
        ),
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

    return ok({"submissions": items, "page": page})


@router.post(
    "/{taskId}/participants/{participantId}/submissions",
    summary="Create Submission",
    dependencies=[Can(TaskPermission.view)],
)
async def post_task_submission(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    contents: list[dict],
    stage: str | None = Query(default=None, max_length=16),
    db=Depends(get_db),
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
) -> dict:
    task = await task_service.get_task(task_id=task_id)
    if task is None:
        raise NotFoundError.for_resource("task", task_id)

    membership = await membership_service.get_membership_by_id(participant_id)
    if membership is None or membership.task_id != task_id:
        raise NotFoundError.for_resource("participant", participant_id)

    if membership.approved != 0:
        raise ForbiddenError(say("submissionClaimNotApproved"))
    if task.ended_at is not None:
        raise BadRequestError(say("submissionChallengeEnded"))
    # A claim's own deadline (set when it is approved, moved by whoever teaches
    # the task) is the last moment to hand work in: past it with nothing in hand
    # the claim reads FAILED (`submission_state`, and the deadline sweep writes
    # the same). A version taken after it would turn that FAILED back into
    # PENDING_REVIEW behind the teacher's back; moving the deadline is how a
    # late hand-in is let through.
    if past_deadline(membership.deadline, datetime.now(UTC)):
        raise BadRequestError(say("submissionPastDeadline"))
    # Each stage is handed in on its own, named by its id, and closes at its
    # own due date.
    stage_at = stage_index(stage, await task_service.stage_keys(task_id))
    await open_stage(db, task_id, stage_at, datetime.now(UTC))

    await can.require(db, auth_user.user_id, ClaimPermission.hand_in, membership)

    if not task.resubmittable:
        existing, _ = await submission_service.list_submissions(
            task_id=task_id,
            participant_id=participant_id,
            stage=stage_at,
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
        stage=stage_at,
    )
    # Commit before answering: the page that just submitted opens the submission
    # history next, and must find what it handed in. Same reason as the commit in
    # ``create_task``.
    await db.commit()
    return ok({"submission": submission_dto})


@router.patch(
    "/{taskId}/participants/{participantId}/submissions/{version}",
    summary="Update Submission",
    dependencies=[Can(TaskPermission.view)],
)
async def patch_task_submission(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    version: Annotated[int, Path(ge=0)],
    contents: list[dict],
    submission_service: TaskSubmissionService = Depends(get_task_submission_service),
    membership_service: TaskMembershipService = Depends(get_task_membership_service),
    task_service: TaskService = Depends(get_task_service),
    auth_user: AuthUserInfo = Depends(require_auth_user),
    db=Depends(get_db),
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
        raise ForbiddenError(say("submissionClaimNotApproved"))
    if task.ended_at is not None:
        raise BadRequestError(say("submissionChallengeEnded"))
    if past_deadline(membership.deadline, datetime.now(UTC)):
        raise BadRequestError(say("submissionPastDeadline"))
    stage = await submission_service.stage_of_version(participant_id, version)
    if stage is not None:
        await open_stage(db, task_id, stage, datetime.now(UTC))

    await can.require(db, auth_user.user_id, ClaimPermission.hand_in, membership)

    submission_dto = await submission_service.modify_submission(
        task_id=task_id,
        participant_id=participant_id,
        submitter_id=auth_user.user_id,
        version=version,
        contents=contents,
    )
    return ok({"submission": submission_dto})


@router.post(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Create Submission Review",
    dependencies=[Can(TaskPermission.teach)],
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
    task, membership = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )

    # 一版可以有多位老师各判一份：冲突只看**这个人**有没有判过。
    existing = await review_service.get_review_dto(submission_id, auth_user.user_id)
    if existing.get("reviewed"):
        raise ConflictError("Review already exists for this submission")

    review_dto = await review_service.create_review(
        submission_id=submission_id,
        reviewer_id=auth_user.user_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
    )
    await _tell_reviewed(db, task, membership, submission_id, review_dto, auth_user)
    return ok({"review": review_dto})


async def _tell_reviewed(db, task, membership, submission_id: int, review: dict, who):
    """The claimer hears the verdict (`task_notices.reviewed`)."""
    if not review.get("reviewed"):
        return
    detail = review.get("detail") or {}
    await task_notices.reviewed(
        db,
        task=task,
        membership=membership,
        submission_id=submission_id,
        accepted=bool(detail.get("accepted")),
        score=int(detail.get("score") or 0),
        by=who.user_id,
        at=datetime.now(UTC),
    )


@router.get(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Get Submission Review",
    dependencies=[Can(TaskPermission.view)],
)
async def get_task_submission_review(
    task_id: Annotated[int, Path(ge=1, alias="taskId")],
    participant_id: Annotated[int, Path(ge=1, alias="participantId")],
    submission_id: Annotated[int, Path(ge=1, alias="submissionId")],
    review_service: TaskSubmissionReviewService = Depends(
        get_task_submission_review_service
    ),
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

    await can.require(db, auth_user.user_id, ClaimPermission.read, membership)

    review_dto = await review_service.get_review_dto(submission_id)

    if not review_dto.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    return ok({"review": review_dto})


async def _update_task_submission_review(
    *,
    task_id: int,
    participant_id: int,
    submission_id: int,
    payload: CreateSubmissionReviewRequest | PatchSubmissionReviewRequest,
    review_service: TaskSubmissionReviewService,
    auth_user: AuthUserInfo,
    db,
) -> dict:
    """The one review update PATCH and PUT both perform.

    PUT requires the whole review while PATCH fills in only what it is given;
    that difference belongs to the request model. The path binding and the write
    stay here so the two verbs cannot drift; both routes declare ``teach``.
    """
    task, membership = await bind_review_path(
        session=db,
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
    )
    review_dto = await review_service.patch_review(
        submission_id=submission_id,
        accepted=payload.accepted,
        score=payload.score,
        comment=payload.comment,
    )
    await _tell_reviewed(db, task, membership, submission_id, review_dto, auth_user)
    return ok({"review": review_dto})


@router.patch(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Re-Review Submission",
    dependencies=[Can(TaskPermission.teach)],
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
    return await _update_task_submission_review(
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
        payload=payload,
        review_service=review_service,
        auth_user=auth_user,
        db=db,
    )


@router.put(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Update Submission Review (Full Replace)",
    dependencies=[Can(TaskPermission.teach)],
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
    return await _update_task_submission_review(
        task_id=task_id,
        participant_id=participant_id,
        submission_id=submission_id,
        payload=payload,
        review_service=review_service,
        auth_user=auth_user,
        db=db,
    )


@router.delete(
    "/{taskId}/participants/{participantId}/submissions/{submissionId}/review",
    summary="Delete Submission Review",
    dependencies=[Can(TaskPermission.teach)],
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

    existing = await review_service.get_review_dto(submission_id)
    if not existing.get("reviewed"):
        raise NotFoundError.for_resource("review", submission_id)

    await review_service.delete_review(submission_id=submission_id)
    review_dto = await review_service.get_review_dto(submission_id)
    return ok({"review": review_dto})
