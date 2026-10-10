"""完成状态这条轴：口径写在这里，推进也写在这里。

``TaskMembership.completion_status`` 回答的是「这个人走到哪一步了」：一版没交、
交了在评审队列里、判通过、被驳回。它的五个取值只在下面写一遍；一条领取该带哪个
取值是**从提交与评审推出来的**，不是记下来的：

- 有 live 提交，其中任一评审 ``accepted``            → ``SUCCESS``
- 否则有 live 提交、它的评审还没有                    → ``PENDING_REVIEW``
- 否则（手上没活）已过截止                            → ``FAILED``
- 否则有 live 提交（每一版的评审都被驳回）            → ``REJECTED_RESUBMITTABLE``
- 否则一版没交、还没过截止                            → ``NOT_SUBMITTED``

「被驳回、过了截止还没重交」落在 ``FAILED``：截止清扫对它写的就是这个值（它在
``indexed_rows.SWEEPABLE_ROWS`` 里，那个谓词同时也是
``ix_task_membership_deadline`` 的索引条件），推导若给出别的值，清扫写下的
``FAILED`` 会被下一次重推（或回填）翻回去，两边来回打架。

``has_work_in_hand`` 就是这份清单的前两条 —— 「手上有活」当且仅当推出来的状态是
``SUCCESS`` 或 ``PENDING_REVIEW``。截止时间清扫任务（``deadline_scheduler``）靠它
决定谁才是「没交东西又过了时间」，两者读的是同一组 ``EXISTS``，不可能各说各话。

每一处能改变答案的写入都调用 :func:`refresh_completion_status`：建提交、改提交、
建评审、改评审、删评审，以及一次性的存量回填。**没有任何地方手写一个状态值** ——
一个没人重算的存量值，正是这一列在交过作业、判通过的领取上还写着 ``NOT_SUBMITTED``
的原因。

同一份优先级还有第二种粒度：板上的题卡片只问「我这条领取走到哪了」，取值那套见下面
的 :func:`claim_state`。它不看截止时间那一档，别的与上面逐条对齐，且顺序由
:func:`completion_status_for` 一处说了算。
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, SQLColumnExpression, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import (
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)

#: 这条轴的五个取值。别处只 import，不要再写一遍字面量。
COMPLETION_STATUS_NOT_SUBMITTED = "NOT_SUBMITTED"
COMPLETION_STATUS_PENDING_REVIEW = "PENDING_REVIEW"
COMPLETION_STATUS_REJECTED_RESUBMITTABLE = "REJECTED_RESUBMITTABLE"
COMPLETION_STATUS_FAILED = "FAILED"
COMPLETION_STATUS_SUCCESS = "SUCCESS"


def _review_passed() -> ColumnElement[bool]:
    """这一版提交的 live 评审存在、且判了通过。相关到 ``TaskSubmission``。"""
    return exists(
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
            TaskSubmissionReview.accepted.is_(True),
        )
        .correlate(TaskSubmission)
    )


def _review_not_written_yet() -> ColumnElement[bool]:
    """这一版提交的评审行压根还没有 —— 它还在队列里。相关到 ``TaskSubmission``。"""
    return ~exists(
        select(TaskSubmissionReview.id)
        .where(
            TaskSubmissionReview.submission_id == TaskSubmission.id,
            TaskSubmissionReview.deleted_at.is_(None),
        )
        .correlate(TaskSubmission)
    )


def _has_submission_where(
    membership_id: SQLColumnExpression[int],
    *review_condition: ColumnElement[bool],
) -> ColumnElement[bool]:
    """这条领取名下有没有一版 live 提交，且它的评审满足给定条件。

    ``membership_id`` 是外层查询里那条领取的 id 列（``TaskMembership.id``），返回的
    ``EXISTS`` 相关到 ``TaskMembership``。软删的提交与评审在它们自己的读者眼里就是
    不存在（本域两个仓库都过滤 ``deleted_at IS NULL``），所以这里也不数它们。

    参数类型写 ``SQLColumnExpression`` 的理由同
    ``app.domain.identity.handles.agent_handle_column``：调用方递进来的是 ORM 映射
    属性（如 ``TaskMembership.id``），类型上不是 ``ColumnElement``，查询里却是。
    """
    return exists(
        select(TaskSubmission.id)
        .where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.deleted_at.is_(None),
            *review_condition,
        )
        .correlate(TaskMembership)
    )


def has_a_passed_submission(
    membership_id: SQLColumnExpression[int],
) -> ColumnElement[bool]:
    """这条领取有活干完了：某一版 live 提交的 live 评审判了通过。"""
    return _has_submission_where(membership_id, _review_passed())


def has_a_submission_in_the_queue(
    membership_id: SQLColumnExpression[int],
) -> ColumnElement[bool]:
    """这条领取交过东西，其中一版还在等评审（那份评审行不存在）。"""
    return _has_submission_where(membership_id, _review_not_written_yet())


def has_a_live_submission(
    membership_id: SQLColumnExpression[int],
) -> ColumnElement[bool]:
    """这条领取交过东西（任意一版，不管评审是什么）。"""
    return _has_submission_where(membership_id)


def has_work_in_hand(membership_id: SQLColumnExpression[int]) -> ColumnElement[bool]:
    """A correlated SQL predicate: does this membership have work in hand?

    「手上有活」= 交过一版、且它的评审要么还没写（还在队列里）要么判了通过；被驳回
    的评审是「退回来了」，不算手上有活。上面那三个谓词里取前两个的并集，所以这条
    句子只有一处定义：截止清扫用它决定谁没交东西，完成状态也用它。
    """
    return _has_submission_where(
        membership_id,
        or_(_review_not_written_yet(), _review_passed()),
    )


def completion_status_for(
    *,
    has_a_passed_submission: bool,
    has_a_submission_in_the_queue: bool,
    has_a_live_submission: bool,
    past_deadline: bool,
) -> str:
    """把三个「有没有」的事实翻成这条轴上的一个取值 —— 唯一的映射。

    顺序就是模块 docstring 里那份清单：判通过压过一切；其次是还在队列里；这两档
    之外就是「手上没活」，过了截止即失败 —— 与截止清扫写的 ``FAILED`` 同一条线
    （清扫只碰 ``~has_work_in_hand`` 的行）；没过截止时，交过但全被驳回是「可重交」，
    一版没交是「未提交」。
    """
    if has_a_passed_submission:
        return COMPLETION_STATUS_SUCCESS
    if has_a_submission_in_the_queue:
        return COMPLETION_STATUS_PENDING_REVIEW
    if past_deadline:
        return COMPLETION_STATUS_FAILED
    if has_a_live_submission:
        return COMPLETION_STATUS_REJECTED_RESUBMITTABLE
    return COMPLETION_STATUS_NOT_SUBMITTED


#: 板上的题卡片那一格（`:func:`claim_state` 的四个取值）—— 与前端
#: ``Claimant['status']`` 同名同义，与 ``completion_status`` 是两套取值、一份优先级。
CLAIM_IN_PROGRESS = "IN_PROGRESS"
CLAIM_SUBMITTED = "SUBMITTED"
CLAIM_PASSED = "PASSED"
CLAIM_REJECTED = "REJECTED"

#: 完成状态的取值 → 卡片那一格。``FAILED`` 到不了这里：卡片这一格不看截止时间。
_CLAIM_BY_COMPLETION_STATUS = {
    COMPLETION_STATUS_SUCCESS: CLAIM_PASSED,
    COMPLETION_STATUS_PENDING_REVIEW: CLAIM_SUBMITTED,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE: CLAIM_REJECTED,
    COMPLETION_STATUS_NOT_SUBMITTED: CLAIM_IN_PROGRESS,
}


def claim_state(verdicts: Iterable[bool | None]) -> str:
    """一个领取现在是什么档位，按它每条 live 提交的判决算。

    ``verdicts`` 是这个领取名下**每条 live 提交**的判决：``True`` 判过、``False``
    退回、``None`` 还没判。空 = 一条都没交。判决与提交的「live」两个条件由取数的
    那一边按 ``has_work_in_hand`` 同一口径给（提交行 ``deleted_at IS NULL``、评审行
    同样），这里只负责排序。

    一条领取可以同时有多版提交：退回之后重交是新的一版，**旧的那版还在**（它只是
    带着一条退回的评审躺着，见 ``TaskSubmissionService.submit_task``）。所以排的是
    优先级，不是「最新那版」：任一版通过就是「已通过」，交出去并被认下来的东西不会
    被后来的一版抹掉。

    优先级本身写在 :func:`completion_status_for` —— 这里只把判决折成它的三个事实再
    翻回来，避免同一份顺序在库里和 Python 里各写一遍（两者分叉的表现就是同一领取在
    「我的」那页与卡片那格显示成两档）。
    """
    # 先落成一份：``verdicts`` 只保证是 Iterable，三次 any() 会把它跑空。
    rows = list(verdicts)
    passed = any(verdict is True for verdict in rows)
    queued = any(verdict is None for verdict in rows)
    handed_in = passed or queued or any(verdict is False for verdict in rows)
    return _CLAIM_BY_COMPLETION_STATUS[
        completion_status_for(
            has_a_passed_submission=passed,
            has_a_submission_in_the_queue=queued,
            has_a_live_submission=handed_in,
            past_deadline=False,
        )
    ]


def past_deadline(deadline: datetime | None, moment: datetime) -> bool:
    """与清扫任务同一条判据：``deadline IS NOT NULL AND deadline < now``。

    交作业那条路（``POST .../submissions``）也按它拒：
    过了自己的截止就不再收新的一版。"""
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        # 库里这一列是 timezone-aware；万一有人塞进一个裸时间，按 UTC 读，别在这里
        # 抛类型错误把一次提交请求变成 500。
        deadline = deadline.replace(tzinfo=UTC)
    return deadline < moment


async def _completion_flags(
    session: AsyncSession,
    membership_id: int,
) -> tuple[bool, bool, bool]:
    """一条领取的三个事实：判通过、还在队列、交过东西。"""
    stmt = select(
        has_a_passed_submission(TaskMembership.id),
        has_a_submission_in_the_queue(TaskMembership.id),
        has_a_live_submission(TaskMembership.id),
    ).where(TaskMembership.id == membership_id)
    row = (await session.execute(stmt)).one()
    return bool(row[0]), bool(row[1]), bool(row[2])


async def derive_completion_status(
    session: AsyncSession,
    membership: TaskMembership,
    *,
    now: datetime | None = None,
) -> str:
    """推这条领取现在该带哪个状态 —— 只读，不写。"""
    passed, queued, handed_in = await _completion_flags(session, membership.id)
    return completion_status_for(
        has_a_passed_submission=passed,
        has_a_submission_in_the_queue=queued,
        has_a_live_submission=handed_in,
        past_deadline=past_deadline(membership.deadline, now or datetime.now(UTC)),
    )


async def refresh_completion_status(
    session: AsyncSession,
    membership: TaskMembership,
    *,
    now: datetime | None = None,
) -> str:
    """重推这条领取的完成状态，**变了才写**，返回推出来的值。

    每一处会改变答案的写入（建/改提交、建/改/删评审）都在自己落库之后调它一次；
    存量回填也在同一个函数上跑，所以库里的值与这条路推出来的值永远是同一个口径。

    变了才写有两层用处：一次提交不必额外产生一次 UPDATE，回填也就天然可重复执行
    （第二遍什么都没变，是 no-op）。
    """
    moment = now or datetime.now(UTC)
    status = await derive_completion_status(session, membership, now=moment)
    if membership.completion_status != status:
        membership.completion_status = status
        membership.updated_at = moment
        session.add(membership)
        await session.flush()
    return status


async def refresh_completion_status_for_submission(
    session: AsyncSession,
    submission_id: int,
    *,
    now: datetime | None = None,
) -> str | None:
    """同上，但从一版提交出发找到它那条领取。

    评审那三条写入（建/改/删）手上只有 submission_id，走这里；找不到对应的提交或
    领取（比如已经被硬删掉）就什么都不做，返回 ``None``。
    """
    membership_id = await session.scalar(
        select(TaskSubmission.membership_id).where(TaskSubmission.id == submission_id)
    )
    if membership_id is None:
        return None
    membership = await session.get(TaskMembership, membership_id)
    if membership is None:
        return None
    return await refresh_completion_status(session, membership, now=now)
