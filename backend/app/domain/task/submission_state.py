"""Whether a 领取 handed work in, asked of the submissions rather than the status.

``TaskMembership.completion_status`` is meant to answer "how far did this person
get": nothing handed in, work sitting in the review queue, passed, or sent back.
But the only writers of that column are the claim path (``NOT_SUBMITTED``) and
the deadline sweep (``FAILED``) — ``create_review`` never advances it. So on a
membership whose work was handed in, the status still reads ``NOT_SUBMITTED``
whether the review is pending or already accepted. Any decision that trusts the
status to say "nothing was submitted" is trusting a value nobody updates.

The submission tables say the truth. A membership has work in hand when it owns
a live submission whose review is either missing (still queued) or accepted
(passed); a rejected review is a hand-back, not work in hand. That sentence has
one home — here — so the deadline sweep and whoever eventually drives the status
axis can read the same definition instead of inventing two.

That is exactly the line the status axis is supposed to draw ("accepted means
success"), which is what this guard must not contradict: it reads the reviews
the same way a writer of ``SUCCESS`` would. What it deliberately does not do is
advance the axis itself; that is a separate, larger change.

The same four states, spelled out rather than collapsed to one yes/no, are what
a 领取 shows on a board card — :func:`claim_state` below draws the finer line
over the very same rows, and a caller that already has the verdicts does not
have to invent its own reading of them.
"""

from collections.abc import Iterable

from sqlalchemy import ColumnElement, SQLColumnExpression, exists, or_, select

from app.domain.task.models import (
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)

#: 「这道题我走到哪了」—— 卡片上那一格，与前端 `Claimant['status']` 同名同义。
#: 取值只看提交与评审，不看 ``completion_status``（理由见上面那段）。
CLAIM_IN_PROGRESS = "IN_PROGRESS"
CLAIM_SUBMITTED = "SUBMITTED"
CLAIM_PASSED = "PASSED"
CLAIM_REJECTED = "REJECTED"


def claim_state(verdicts: Iterable[bool | None]) -> str:
    """一个领取现在是什么档位，按它每条 live 提交的判决算。

    ``verdicts`` 是这个领取名下**每条 live 提交**的判决：``True`` 判过、``False``
    退回、``None`` 还没判。空 = 一条都没交。判决与提交的「live」两个条件由取数的
    那一边按 ``has_work_in_hand`` 同一口径给（提交行 ``deleted_at IS NULL``、评审行
    同样），这里只负责排序，不重复判一次。

    一条领取可以同时有多版提交：退回之后重交是新的一版，**旧的那版还在**（它只是
    带着一条退回的评审躺着，见 ``TaskSubmissionService.submit_task``）。所以排的是
    优先级，不是「最新那版」：

    - 任一版通过 → 已通过 —— 交出去并被认下来的东西不会被后来的一版抹掉；
    - 没有通过的，但有还没判的 → 已提交 —— 退回后重交就是这一档，人正在队列里；
    - 剩下的（每条都判了，没有一条通过）→ 未通过，可以重交；
    - 一条提交都没有 → 进行中。
    """
    handed_back = False
    queued = False
    for verdict in verdicts:
        if verdict is True:
            return CLAIM_PASSED
        if verdict is None:
            queued = True
        else:
            handed_back = True
    if queued:
        return CLAIM_SUBMITTED
    if handed_back:
        return CLAIM_REJECTED
    return CLAIM_IN_PROGRESS


def has_work_in_hand(membership_id: SQLColumnExpression[int]) -> ColumnElement[bool]:
    """A correlated SQL predicate: does this membership have work in hand?

    ``membership_id`` is the membership-id column of the enclosing query (its
    ``TaskMembership.id``), which is also what the returned ``EXISTS``
    correlates against. Soft-deleted submissions and reviews are not there as
    far as their own readers in this domain are concerned — the repositories
    filter ``deleted_at IS NULL`` on both — so neither is counted here.

    The parameter is typed ``SQLColumnExpression`` for the same reason as
    ``app.domain.identity.handles.agent_handle_column``: callers hand in an ORM
    mapped attribute such as ``TaskMembership.id``, which is not a
    ``ColumnElement`` in the types even though it is one in the query.
    """
    # The review the membership is still waiting on: no live review row at all.
    still_in_the_queue = ~exists(
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        .correlate(TaskSubmission)
    )
    # The review that says the work passed.
    passed = exists(
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
            TaskSubmissionReview.accepted.is_(True),
        )
        .correlate(TaskSubmission)
    )
    return exists(
        select(TaskSubmission.id)
        .where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.deleted_at.is_(None),
            or_(still_in_the_queue, passed),
        )
        .correlate(TaskMembership)
    )
