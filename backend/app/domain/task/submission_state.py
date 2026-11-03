"""完成状态这条轴：口径写在这里，推进也写在这里。

一道题分成若干阶段（``app.domain.task.stages``；不设阶段就是一个阶段），每个阶段
单独提交、单独批改。**一个阶段走到哪一步，看它所有的 live 提交**，按这个优先级：

- 有一版的一份评审判了通过                   → ``PASSED``（已通过）
- 否则有一版的评审还没写                     → ``PENDING_REVIEW``（待批改）
- 否则交过、每一版都被退回                   → ``REJECTED``（被退回）
- 否则（一版没交）                           → ``NOT_SUBMITTED``（未提交）

不分阶段的题就是一个阶段，所以这四条也就是没有阶段以前的那份口径。

``TaskMembership.completion_status`` 回答的是「这条领取走到哪一步了」，由各阶段的
状态推出来，不是记下来的：

- 每个阶段都已通过                            → ``SUCCESS``
- 否则有阶段在等批改                          → ``PENDING_REVIEW``
- 否则已过时限                                → ``FAILED``
- 否则有阶段被退回                            → ``REJECTED_RESUBMITTABLE``
- 否则                                        → ``NOT_SUBMITTED``

「已过时限」是两种之一：领取自己的截止时间过了，或者有一个阶段过了它的截止时间、
手上却没有这个阶段的活（没交，或者被退回）。过了截止的阶段不再收新的一版（交作业那条
路按 :func:`past_deadline` 拒），所以这条领取再也走不完，读作 ``FAILED``。

「被驳回、过了截止还没重交」落在 ``FAILED``：截止清扫对它写的就是这个值（它在
``indexed_rows.SWEEPABLE_ROWS`` 里，那个谓词同时也是 ``ix_task_membership_deadline``
的索引条件）。清扫对每条候选领取用同一个 :func:`derive_completion_status` 推一遍，
推出 ``FAILED`` 才写，所以两边不会来回打架。

每一处能改变答案的写入都调用 :func:`refresh_completion_status`：建提交、改提交、
建评审、改评审、删评审、改题目的阶段（:func:`refresh_task_claims`），以及一次性的
存量回填。**没有任何地方手写一个状态值。**

同一份优先级还有第二种粒度：板上的题卡片只问「我这条领取走到哪了」，取值那套见下面
的 :func:`claim_state`。它不看时限那一档，别的与上面逐条对齐，且顺序由
:func:`completion_status_for` 一处说了算。
"""

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, SQLColumnExpression, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.models import (
    TaskMembership,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.task.stages import (
    StageLine,
    list_stages,
    planned_stage_change,
    stage_count,
)
from app.domain.team.models import Team
from app.domain.user.models import User, UserProfile

#: 这条轴的五个取值。别处只 import，不要再写一遍字面量。
COMPLETION_STATUS_NOT_SUBMITTED = "NOT_SUBMITTED"
COMPLETION_STATUS_PENDING_REVIEW = "PENDING_REVIEW"
COMPLETION_STATUS_REJECTED_RESUBMITTABLE = "REJECTED_RESUBMITTABLE"
COMPLETION_STATUS_FAILED = "FAILED"
COMPLETION_STATUS_SUCCESS = "SUCCESS"

#: 一个阶段的四个取值（前端 ``TaskStageStatus`` 同名同义）。
STAGE_NOT_SUBMITTED = "NOT_SUBMITTED"
STAGE_PENDING_REVIEW = "PENDING_REVIEW"
STAGE_PASSED = "PASSED"
STAGE_REJECTED = "REJECTED"
STAGE_STATUSES = (
    STAGE_NOT_SUBMITTED,
    STAGE_PENDING_REVIEW,
    STAGE_PASSED,
    STAGE_REJECTED,
)
#: 读给人看的第五个取值：这个阶段过了截止、一版也没交，这条领取因它未通过
#: （:func:`out_of_time`）。领取自己的状态（:func:`stage_states_of`）和出题人看的
#: 分布（:func:`stage_distribution`）里有它；完成状态的推导用的是上面那四个。
STAGE_MISSED = "MISSED"


@dataclass(frozen=True)
class HandedIn:
    """一版 live 提交和它的判决：``True`` 有一份 live 评审判了通过、``False`` 评审
    都判了退回、``None`` 还没有 live 评审。"""

    stage: int
    version: int
    verdict: bool | None


def stage_states(handed_in: Iterable[HandedIn], count: int) -> list[str]:
    """每个阶段现在的状态，按它每一版 live 提交的判决。指向一个题目已经没有的阶段的
    提交不算。

    排的是优先级，不是「最新那一版」：任一版通过，这个阶段就是「已通过」—— 交出去
    并被认下来的东西不会被后来的一版抹掉；否则有一版还没判就是「待批改」；否则交过
    而都被退回是「被退回」。退回之后重交是新的一版，旧的那版带着退回的评审还在。
    """
    verdicts: dict[int, list[bool | None]] = {}
    for row in handed_in:
        if 0 <= row.stage < count:
            verdicts.setdefault(row.stage, []).append(row.verdict)
    states = []
    for index in range(count):
        rows = verdicts.get(index, [])
        if any(v is True for v in rows):
            states.append(STAGE_PASSED)
        elif any(v is None for v in rows):
            states.append(STAGE_PENDING_REVIEW)
        elif rows:
            states.append(STAGE_REJECTED)
        else:
            states.append(STAGE_NOT_SUBMITTED)
    return states


def completion_status_for(states: Sequence[str], *, out_of_time: bool) -> str:
    """把各阶段的状态翻成这条轴上的一个取值 —— 唯一的映射，顺序就是模块 docstring
    里那份清单。"""
    if states and all(s == STAGE_PASSED for s in states):
        return COMPLETION_STATUS_SUCCESS
    if any(s == STAGE_PENDING_REVIEW for s in states):
        return COMPLETION_STATUS_PENDING_REVIEW
    if out_of_time:
        return COMPLETION_STATUS_FAILED
    if any(s == STAGE_REJECTED for s in states):
        return COMPLETION_STATUS_REJECTED_RESUBMITTABLE
    return COMPLETION_STATUS_NOT_SUBMITTED


def past_deadline(deadline: datetime | None, moment: datetime) -> bool:
    """与清扫任务同一条判据：``deadline IS NOT NULL AND deadline < now``。

    交作业那条路（``POST .../submissions``）也按它拒：过了自己的截止、或者过了这个
    阶段的截止，就不再收新的一版。"""
    if deadline is None:
        return False
    if deadline.tzinfo is None:
        # 库里这一列是 timezone-aware；万一有人塞进一个裸时间，按 UTC 读，别在这里
        # 抛类型错误把一次提交请求变成 500。
        deadline = deadline.replace(tzinfo=UTC)
    return deadline < moment


def out_of_time(
    states: Sequence[str],
    *,
    claim_deadline: datetime | None,
    stage_dues: Sequence[datetime | None],
    moment: datetime,
) -> bool:
    """领取的截止过了，或者有一个阶段过了截止、手上没有它的活。"""
    if past_deadline(claim_deadline, moment):
        return True
    return any(
        past_deadline(due, moment) and state in (STAGE_NOT_SUBMITTED, STAGE_REJECTED)
        for state, due in zip(states, stage_dues, strict=False)
    )


#: 板上的题卡片那一格（`:func:`claim_state` 的四个取值）—— 与前端
#: ``Claimant['status']`` 同名同义，与 ``completion_status`` 是两套取值、一份优先级。
CLAIM_IN_PROGRESS = "IN_PROGRESS"
CLAIM_SUBMITTED = "SUBMITTED"
CLAIM_PASSED = "PASSED"
CLAIM_REJECTED = "REJECTED"

#: 完成状态的取值 → 卡片那一格。``FAILED`` 到不了这里：卡片这一格不看时限。
_CLAIM_BY_COMPLETION_STATUS = {
    COMPLETION_STATUS_SUCCESS: CLAIM_PASSED,
    COMPLETION_STATUS_PENDING_REVIEW: CLAIM_SUBMITTED,
    COMPLETION_STATUS_REJECTED_RESUBMITTABLE: CLAIM_REJECTED,
    COMPLETION_STATUS_NOT_SUBMITTED: CLAIM_IN_PROGRESS,
}


def claim_state(states: Sequence[str]) -> str:
    """一个领取现在是什么档位，按它各阶段的状态算（:func:`stage_states`）。

    优先级本身写在 :func:`completion_status_for` —— 这里只是不看时限地读它，避免同一
    份顺序在两处各写一遍（两者分叉的表现就是同一领取在「我的」那页与卡片那格显示成
    两档）。
    """
    return _CLAIM_BY_COMPLETION_STATUS[completion_status_for(states, out_of_time=False)]


def has_a_live_submission(
    membership_id: SQLColumnExpression[int],
) -> ColumnElement[bool]:
    """这条领取交过东西（任意阶段、任意一版，不管评审是什么）。相关到
    ``TaskMembership``，``membership_id`` 是外层查询里那条领取的 id 列。"""
    return exists(
        select(TaskSubmission.id)
        .where(
            TaskSubmission.membership_id == membership_id,
            TaskSubmission.deleted_at.is_(None),
        )
        .correlate(TaskMembership)
    )


async def handed_in_for(
    session: AsyncSession, membership_ids: Sequence[int]
) -> dict[int, list[HandedIn]]:
    """这些领取名下**每一版 live 提交**和它的判决，按领取分组。

    两个「live」：提交行与评审行各自 ``deleted_at IS NULL``。一版可以有多位老师各判
    一份，任一份判了通过就是通过。一页题一次问完，不按题各发一条。
    """
    if not membership_ids:
        return {}
    reviews = func.count(TaskSubmissionReview.id)
    passed = func.bool_or(TaskSubmissionReview.accepted)
    stmt = (
        select(
            TaskSubmission.membership_id,
            TaskSubmission.stage,
            TaskSubmission.version,
            reviews,
            passed,
        )
        .select_from(TaskSubmission)
        .outerjoin(
            TaskSubmissionReview,
            and_(
                TaskSubmissionReview.submission_id == TaskSubmission.id,
                TaskSubmissionReview.deleted_at.is_(None),
            ),
        )
        .where(
            TaskSubmission.membership_id.in_(membership_ids),
            TaskSubmission.deleted_at.is_(None),
        )
        .group_by(
            TaskSubmission.id,
            TaskSubmission.membership_id,
            TaskSubmission.stage,
            TaskSubmission.version,
        )
    )
    grouped: dict[int, list[HandedIn]] = {}
    for membership_id, stage, version, count, accepted in (
        await session.execute(stmt)
    ).all():
        grouped.setdefault(int(membership_id), []).append(
            HandedIn(
                stage=int(stage),
                version=int(version),
                verdict=None if not count else bool(accepted),
            )
        )
    return grouped


def missed(
    states: Sequence[str], stage_dues: Sequence[datetime | None], moment: datetime
) -> list[str]:
    """一条获批的领取读到的各阶段状态：过了截止、一版也没交的阶段读
    ``STAGE_MISSED``（这条领取因它未通过），不再读「未提交」。"""
    return [
        STAGE_MISSED
        if state == STAGE_NOT_SUBMITTED
        and index < len(stage_dues)
        and past_deadline(stage_dues[index], moment)
        else state
        for index, state in enumerate(states)
    ]


async def stage_states_of(
    session: AsyncSession,
    membership: TaskMembership,
    *,
    now: datetime | None = None,
) -> list[str]:
    """这条领取每个阶段现在的状态，读给人和它的 agent 看：获批的领取错过了截止的
    阶段读 ``STAGE_MISSED``。"""
    stages = await list_stages(session, membership.task_id)
    handed_in = (await handed_in_for(session, [membership.id])).get(membership.id, [])
    states = stage_states(handed_in, stage_count(stages))
    # Only an approved claim hands work in, so only it can miss a stage.
    if membership.approved != 0:
        return states
    return missed(states, [s.due_at for s in stages], now or datetime.now(UTC))


async def stage_distribution(
    session: AsyncSession, task_id: int
) -> list[dict[str, int]]:
    """这道题每个阶段上，获批的领取各走到哪一步：每阶段一份 ``{状态: 人数}``。
    和领取自己读到的一样（:func:`stage_states_of`），过了截止一版没交的数在
    ``STAGE_MISSED``（未通过）里，不在「未提交」里。"""
    stages = await list_stages(session, task_id)
    count = stage_count(stages)
    dues = [s.due_at for s in stages]
    moment = datetime.now(UTC)
    claims = list(
        (
            await session.execute(
                select(TaskMembership.id).where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                    TaskMembership.approved == 0,
                )
            )
        )
        .scalars()
        .all()
    )
    handed_in = await handed_in_for(session, claims)
    tallies = [
        Counter({s: 0 for s in (*STAGE_STATUSES, STAGE_MISSED)}) for _ in range(count)
    ]
    for membership_id in claims:
        states = stage_states(handed_in.get(membership_id, []), count)
        for index, state in enumerate(missed(states, dues, moment)):
            tallies[index][state] += 1
    return [dict(t) for t in tallies]


async def derive_completion_status(
    session: AsyncSession,
    membership: TaskMembership,
    *,
    now: datetime | None = None,
) -> str:
    """推这条领取现在该带哪个状态 —— 只读，不写。"""
    stages = await list_stages(session, membership.task_id)
    handed_in = (await handed_in_for(session, [membership.id])).get(membership.id, [])
    return _derived(
        membership,
        stage_states(handed_in, stage_count(stages)),
        [s.due_at for s in stages],
        now or datetime.now(UTC),
    )


def _derived(
    membership: TaskMembership,
    states: Sequence[str],
    stage_dues: Sequence[datetime | None],
    moment: datetime,
) -> str:
    return completion_status_for(
        states,
        out_of_time=out_of_time(
            states,
            claim_deadline=membership.deadline,
            # Only an approved claim hands work in, so only it can miss a stage.
            stage_dues=stage_dues if membership.approved == 0 else [],
            moment=moment,
        ),
    )


@dataclass(frozen=True)
class StageChangeEffect:
    """一次改阶段会让哪些领取跨过「未通过」那条线：``failing`` 现在不是、改完会是
    ``FAILED``；``reopening`` 现在是、改完不再是。各自按领取的先后列出参与者的名字
    （个人的昵称，团队的队名）；``failing_stages`` 是让前一种人判为未通过的那几个阶段
    （改完之后过了截止、还没交），按阶段的顺序。"""

    failing_names: tuple[str, ...] = ()
    reopening_names: tuple[str, ...] = ()
    failing_stages: tuple[str, ...] = ()

    @property
    def failing(self) -> int:
        return len(self.failing_names)

    @property
    def reopening(self) -> int:
        return len(self.reopening_names)


async def _claimant_names(
    session: AsyncSession, claims: Sequence[TaskMembership]
) -> dict[int, str]:
    """每条领取的参与者叫什么：个人的昵称（没有就用户名），团队的队名。"""
    people = [c.member_id for c in claims if not c.is_team]
    teams = [c.member_id for c in claims if c.is_team]
    users = (
        {
            user_id: nickname or username
            for user_id, username, nickname in await session.execute(
                select(User.id, User.username, UserProfile.nickname)
                .outerjoin(UserProfile, UserProfile.user_id == User.id)
                .where(User.id.in_(people))
            )
        }
        if people
        else {}
    )
    team_names = (
        {
            row.id: row.name
            for row in await session.scalars(select(Team).where(Team.id.in_(teams)))
        }
        if teams
        else {}
    )
    return {
        c.id: (team_names if c.is_team else users).get(c.member_id, "") for c in claims
    }


async def stage_change_effect(
    session: AsyncSession,
    task_id: int,
    lines: Sequence[StageLine],
    *,
    now: datetime | None = None,
) -> StageChangeEffect:
    """把题目的阶段存成 ``lines`` 会对它的领取做什么 —— 只算，不写。

    和存的那一次走同一套：已交的提交按阶段的 id 跟着它的阶段走
    （``replace_stages`` 的 ``stage_moves``），每条领取用同一个推导重推一遍
    （:func:`refresh_task_claims`）；比的是它现在记着的那个状态，也就是人现在看到的。
    出题人存之前据此知道会有几个人被判未通过、几个人回到进行中。"""
    moment = now or datetime.now(UTC)
    # The save's own check: a change it would refuse is refused here too.
    change = await planned_stage_change(session, task_id, lines)
    moved = change.moved
    count = stage_count(change.lines)
    dues = [line.due_at for line in change.lines]
    claims = (
        (
            await session.execute(
                select(TaskMembership).where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    handed_in = await handed_in_for(session, [claim.id for claim in claims])
    failing: list[TaskMembership] = []
    reopening: list[TaskMembership] = []
    missed: set[int] = set()
    for claim in sorted(claims, key=lambda c: c.id):
        rows = [
            HandedIn(stage=moved[row.stage], version=row.version, verdict=row.verdict)
            for row in handed_in.get(claim.id, [])
            if row.stage in moved
        ]
        states = stage_states(rows, count)
        after = _derived(claim, states, dues, moment)
        was_failed = claim.completion_status == COMPLETION_STATUS_FAILED
        if after == COMPLETION_STATUS_FAILED and not was_failed:
            failing.append(claim)
            missed.update(
                index
                for index, (state, due) in enumerate(zip(states, dues, strict=False))
                if past_deadline(due, moment)
                and state in (STAGE_NOT_SUBMITTED, STAGE_REJECTED)
            )
        elif was_failed and after != COMPLETION_STATUS_FAILED:
            reopening.append(claim)
    names = await _claimant_names(session, [*failing, *reopening])
    return StageChangeEffect(
        failing_names=tuple(names[c.id] for c in failing),
        reopening_names=tuple(names[c.id] for c in reopening),
        failing_stages=tuple(change.lines[i].name for i in sorted(missed)),
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


async def refresh_task_claims(
    session: AsyncSession, task_id: int, *, now: datetime | None = None
) -> None:
    """重推这道题每条领取的完成状态。

    题目的阶段一变（加、删、挪、改截止），每条领取推出来的答案都可能跟着变：多了一个
    已过截止的阶段，原来的 SUCCESS 就不再成立；删掉一个空着的阶段，原来的「未提交」
    就成了 SUCCESS。改阶段的那一次写入在落库之后调它，和提交、评审一样在同一个事务里。
    """
    claims = (
        (
            await session.execute(
                select(TaskMembership).where(
                    TaskMembership.task_id == task_id,
                    TaskMembership.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for membership in claims:
        await refresh_completion_status(session, membership, now=now)


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
