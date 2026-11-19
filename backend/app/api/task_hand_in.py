"""Handing a version in to a 题目: the checks it passes and what follows it.

One path for whoever hands in — the person on the 题目 page
(`routes/tasks/submissions.post_task_submission`), and the project's AI
teammate for that person on their word (`routes/topics_hand_in`) — so a version
the teammate hands in is refused, recorded, versioned and judged exactly as one
handed in on the page.
"""

from datetime import UTC, datetime

from app.api.task_claims import project_of_claim
from app.core.errors import (
    BadRequestError,
    ConflictError,
    ForbiddenError,
)
from app.core.sentences import NoticeText, say
from app.domain.task import code_version, judge_queue
from app.domain.task.services import TaskSubmissionService
from app.domain.task.stages import open_stage
from app.domain.task.submission_state import past_deadline


async def refuse_closed(db, *, task, membership, stage: int) -> None:
    """Refuse a hand-in on a claim not approved, to a 题目 that ended, past the
    claim's own deadline, or to a stage the 题目 does not have or that is past
    its due date.

    A claim's own deadline (set when it is approved, moved by whoever teaches
    the 题目) is the last moment to hand work in: past it with nothing in hand
    the claim reads FAILED (`submission_state`, and the deadline sweep writes
    the same). A version taken after it would turn that FAILED back into
    PENDING_REVIEW behind the teacher's back; moving the deadline is how a late
    hand-in is let through. Each stage is handed in on its own and closes at
    its own due date."""
    if membership.approved != 0:
        raise ForbiddenError(say("submissionClaimNotApproved"))
    if task.ended_at is not None:
        raise BadRequestError(say("submissionChallengeEnded"))
    if past_deadline(membership.deadline, datetime.now(UTC)):
        raise BadRequestError(say("submissionPastDeadline"))
    await open_stage(db, task.id, stage, datetime.now(UTC))


async def refuse_used_up(
    submissions: TaskSubmissionService, *, task, membership, stage: int
) -> None:
    """Refuse a second version to a stage of a 题目 that takes one."""
    if task.resubmittable:
        return
    existing, _ = await submissions.list_submissions(
        task_id=task.id,
        participant_id=membership.id,
        stage=stage,
        all_versions=False,
        query_review=False,
        limit=1,
    )
    if existing:
        raise BadRequestError(say("submissionNotResubmittable"))


async def hand_in_version(
    db,
    *,
    task,
    membership,
    stage: int,
    submitter_id: int,
    contents: list[dict],
    submissions: TaskSubmissionService,
    code_sha: str | None = None,
    moved: NoticeText | None = None,
) -> dict:
    """Hand ``contents`` in as ``submitter_id`` and queue the judging, once the
    checks above passed. Not committed: the caller commits.

    The version records the code it is judged on (方案 §数据结构): the commit
    the project's default branch points at now; left empty when it cannot be
    read — handing in must not fail because the forge is slow — and for a
    project on GitHub (step 1 judges hosted repositories only). With
    ``code_sha`` given, that is the version the hand-in was shown, and a branch
    that moved on since is refused with ``moved``
    (``submissionCodeMoved`` by default)."""
    project = await project_of_claim(
        db, task=task, membership=membership, user_id=submitter_id
    )
    pending = (
        await code_version.for_project(db, project.id) if project is not None else None
    )
    if code_sha and pending is not None and pending.sha != code_sha:
        raise ConflictError(moved or say("submissionCodeMoved"))

    submission = await submissions.submit_task(
        task_id=task.id,
        participant_id=membership.id,
        submitter_id=submitter_id,
        contents=contents,
        stage=stage,
        code_sha=pending.sha if pending is not None else "",
    )
    # A stage with test points and a recorded code version is judged once
    # (方案 §一次评测怎么跑); a version of code already judged reuses that
    # result (``judge_queue``).
    await judge_queue.enqueue_for_submission(
        db,
        task_id=task.id,
        stage=stage,
        submission_id=int(submission["id"]),
        project_id=project.id if project is not None else None,
        code_sha=pending.sha if pending is not None else "",
        requested_by=submitter_id,
    )
    return submission
