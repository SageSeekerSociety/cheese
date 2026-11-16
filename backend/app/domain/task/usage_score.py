"""额度用量：一份领题在这道题上用掉了多少额度，和评分要点里 ``usage`` 那一行给几分。

一份领题开出一个项目（个人领题开在他的私人小队，团队领题开在队伍，见
``claims.started_at``）。项目里每次调模型都在用量账本 ``resource_usage`` 记一行
额度；这份领题的用量就是它那个项目的额度之和。导师也能在学生项目里调 AI，那些
行记在导师名下（``credit_owner_handle`` 是领题时同意的某位导师），不算进学生的
用量。没有记名的行照算。

``usage`` 行两种给分（``rubric.USAGE_MODES``）：

- ``threshold``：用量不超过 ``full`` 得满分，达到 ``zero`` 得 0 分，中间按比例，
  四舍五入到整数。只看自己，老师写评审那一刻定下。
- ``rank``：在交过作业的领题里比，满分 ×（比你用得多的份数）÷（份数 − 1），并列
  同分，只有一份时满分。别人后交会改变名次，所以题目截止（``Task.deadline``）前
  一律是暂定；截止后的清扫（``finalize_ranked_reviews``）把评审里的暂定分换成定稿
  分。题目没有截止时间时一直是暂定。

用量和分数只按人放在批改里（名单、评审、学生自己的提交），不进学情分析。
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime

from sqlalchemy import and_, cast, exists, func, not_, or_, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.project.models import Project
from app.domain.task.models import (
    Task,
    TaskMembership,
    TaskRubricItem,
    TaskSubmission,
    TaskSubmissionReview,
)
from app.domain.task.rubric import USAGE, USAGE_RANK, USAGE_THRESHOLD
from app.domain.team.models import Team
from app.domain.usage.models import ResourceUsage


def threshold_score(credits: float, points: int, full: float, zero: float) -> int:
    """``threshold`` 的分：``full`` 及以下满分，``zero`` 及以上 0 分，中间线性。"""
    if credits <= full:
        return points
    if credits >= zero or zero <= full:
        return 0
    return int(round(points * (zero - credits) / (zero - full) + 1e-9))


def rank_scores(
    credits: Mapping[int, float], points: int
) -> dict[int, tuple[int, int, int]]:
    """``rank`` 的分：每份领题 →（分，名次，参与份数）。用得越少名次越前，名次是
    比它用得少的份数加一，并列同名次同分。"""
    values = list(credits.values())
    n = len(values)
    out: dict[int, tuple[int, int, int]] = {}
    for key, mine in credits.items():
        more = sum(1 for v in values if v > mine)
        less = sum(1 for v in values if v < mine)
        score = points if n < 2 else int(round(points * more / (n - 1) + 1e-9))
        out[key] = (score, less + 1, n)
    return out


def _claim_rows(task_id: int, membership_ids: Sequence[int] | None):
    """领题 ⋈ 它开出的项目 ⋈ 账本行，只留不记在导师名下的行。"""
    mentors = func.coalesce(TaskMembership.agreed_mentors, cast("[]", JSONB))
    where = [
        TaskMembership.task_id == task_id,
        TaskMembership.deleted_at.is_(None),
        or_(
            ResourceUsage.credit_owner_handle.is_(None),
            not_(mentors.op("?")(ResourceUsage.credit_owner_handle)),
        ),
    ]
    if membership_ids is not None:
        where.append(TaskMembership.id.in_(membership_ids))
    return where, [
        (
            Project,
            Project.external_task_id == TaskMembership.task_id,
        ),
        (Team, Team.id == Project.team_id),
        (ResourceUsage, ResourceUsage.project_id == Project.id),
    ]


def _owns():
    return or_(
        and_(
            TaskMembership.is_team.is_(True),
            Project.team_id == TaskMembership.member_id,
        ),
        and_(
            TaskMembership.is_team.is_(False),
            Team.personal_owner_user_id == TaskMembership.member_id,
        ),
    )


async def credits_by_claim(
    session: AsyncSession, task_id: int, membership_ids: Sequence[int] | None = None
) -> dict[int, float]:
    """这道题每份领题用掉的额度；没开项目或一次没调过模型的领题不在里面（读作 0）。"""
    if membership_ids is not None and not membership_ids:
        return {}
    where, joins = _claim_rows(task_id, membership_ids)
    stmt = select(TaskMembership.id, func.sum(ResourceUsage.credits))
    for target, on in joins:
        stmt = stmt.join(target, on)
    stmt = stmt.where(*where, _owns()).group_by(TaskMembership.id)
    return {mid: float(total or 0) for mid, total in await session.execute(stmt)}


async def credits_by_model(
    session: AsyncSession, task_id: int, membership_id: int
) -> list[dict]:
    """一份领题按模型分的额度，多的在前。"""
    where, joins = _claim_rows(task_id, [membership_id])
    total = func.sum(ResourceUsage.credits)
    stmt = select(ResourceUsage.model, total)
    for target, on in joins:
        stmt = stmt.join(target, on)
    stmt = (
        stmt.where(*where, _owns()).group_by(ResourceUsage.model).order_by(total.desc())
    )
    return [
        {"model": model or "", "credits": round(float(c or 0), 2)}
        for model, c in await session.execute(stmt)
        if c
    ]


async def usage_line(session: AsyncSession, task_id: int) -> TaskRubricItem | None:
    """这道题评分要点里的 ``usage`` 行；一道题最多一行（``rubric``）。"""
    return await session.scalar(
        select(TaskRubricItem).where(
            TaskRubricItem.task_id == task_id, TaskRubricItem.kind == USAGE
        )
    )


async def handed_in(session: AsyncSession, task_id: int) -> set[int]:
    """这道题交过至少一版的领题。"""
    rows = await session.scalars(
        select(TaskMembership.id).where(
            TaskMembership.task_id == task_id,
            TaskMembership.deleted_at.is_(None),
            exists().where(
                TaskSubmission.membership_id == TaskMembership.id,
                TaskSubmission.deleted_at.is_(None),
            ),
        )
    )
    return set(rows)


def is_final(task: Task, mode: str, now: datetime | None = None) -> bool:
    """这一行的分定下了没有：阈值总是定的；排名到题目截止才定。"""
    if mode != USAGE_RANK:
        return True
    return task.deadline is not None and (now or datetime.now(UTC)) >= task.deadline


async def usage_of_claims(
    session: AsyncSession, task: Task, membership_ids: Iterable[int]
) -> dict[int, dict]:
    """每份领题给读者的那一块：用了多少额度，题目有 ``usage`` 行时再加它的分。

    ``score`` 为 None 是这份领题还没交过作业（排名只比交了的，阈值也等交了再算）。
    """
    ids = list(membership_ids)
    if not ids:
        return {}
    line = await usage_line(session, task.id)
    if line is None:
        spent = await credits_by_claim(session, task.id, ids)
        return {mid: {"credits": round(spent.get(mid, 0.0), 2)} for mid in ids}
    config = line.config or {}
    mode = str(config.get("mode") or USAGE_THRESHOLD)
    done = await handed_in(session, task.id)
    if mode == USAGE_RANK:
        # 名次要和全班比，所以读这道题全部领题的用量。
        spent = await credits_by_claim(session, task.id)
        ranked = rank_scores({mid: spent.get(mid, 0.0) for mid in done}, line.points)
    else:
        spent = await credits_by_claim(session, task.id, ids)
        ranked = {}
    final = is_final(task, mode)
    out: dict[int, dict] = {}
    for mid in ids:
        credits = spent.get(mid, 0.0)
        view: dict = {
            "credits": round(credits, 2),
            "line": {
                "index": line.index,
                "points": line.points,
                "mode": mode,
                **(
                    {"full": config.get("full"), "zero": config.get("zero")}
                    if mode == USAGE_THRESHOLD
                    else {}
                ),
            },
            "score": None,
            "final": final,
        }
        if mid in done:
            if mode == USAGE_RANK:
                score, rank, n = ranked[mid]
                view.update(score=score, rank=rank, ranked=n)
            else:
                view["score"] = threshold_score(
                    credits, line.points, float(config["full"]), float(config["zero"])
                )
        out[mid] = view
    return out


async def compose_review(
    session: AsyncSession,
    *,
    task: Task,
    membership: TaskMembership,
    submission_id: int,
    accepted: bool,
    score: int,
) -> tuple[int, dict | None]:
    """一条评审要记下的分。

    题目在这一版的阶段上有 ``usage`` 行、而且判的是通过时，老师填的 ``score`` 是
    手动几项的分（0 到这一阶段手动项的分值之和），总分 = 它 + 这一刻 ``usage`` 行
    的分，怎么来的记进 ``auto_scores``。其余情况 ``score`` 原样就是总分。
    """
    line = await usage_line(session, task.id)
    if line is None or not accepted:
        return score, None
    stage = await session.scalar(
        select(TaskSubmission.stage).where(TaskSubmission.id == submission_id)
    )
    if (stage or 0) != line.stage:
        return score, None
    stage_total = await session.scalar(
        select(func.coalesce(func.sum(TaskRubricItem.points), 0)).where(
            TaskRubricItem.task_id == task.id, TaskRubricItem.stage == line.stage
        )
    )
    cap = int(stage_total or 0) - line.points
    if not 0 <= score <= cap:
        raise BadRequestError(say("reviewManualScoreRange", max=cap))
    view = (await usage_of_claims(session, task, [membership.id]))[membership.id]
    auto = int(view.get("score") or 0)
    return score + auto, {
        "manual": score,
        "final": bool(view["final"]),
        "lines": [
            {
                "index": line.index,
                "kind": USAGE,
                "points": line.points,
                "score": auto,
                "credits": view["credits"],
                "mode": view["line"]["mode"],
            }
        ],
    }


async def finalize_ranked_reviews(session: AsyncSession) -> int:
    """把截止已过的排名题上、还记着暂定分的评审换成定稿分。返回改了几条。"""
    now = datetime.now(UTC)
    pending = (
        await session.execute(
            select(TaskSubmissionReview, TaskMembership.id, Task)
            .join(
                TaskSubmission, TaskSubmission.id == TaskSubmissionReview.submission_id
            )
            .join(TaskMembership, TaskMembership.id == TaskSubmission.membership_id)
            .join(Task, Task.id == TaskMembership.task_id)
            .where(
                TaskSubmissionReview.deleted_at.is_(None),
                TaskSubmissionReview.auto_scores.isnot(None),
                TaskSubmissionReview.auto_scores["final"].astext == "false",
                Task.deadline.isnot(None),
                Task.deadline <= now,
            )
        )
    ).all()
    by_task: dict[int, tuple[Task, list]] = {}
    for review, mid, task in pending:
        by_task.setdefault(task.id, (task, []))[1].append((review, mid))
    changed = 0
    for task, reviews in by_task.values():
        views = await usage_of_claims(session, task, {mid for _, mid in reviews})
        for review, mid in reviews:
            view = views.get(mid) or {}
            record = dict(review.auto_scores or {})
            lines = [dict(entry) for entry in record.get("lines") or []]
            auto = int(view.get("score") or 0)
            for entry in lines:
                if entry.get("kind") == USAGE:
                    entry["score"] = auto
                    entry["credits"] = view.get("credits", entry.get("credits"))
            record.update(lines=lines, final=True)
            review.auto_scores = record
            review.score = int(record.get("manual") or 0) + sum(
                int(entry.get("score") or 0) for entry in lines
            )
            review.updated_at = now
            changed += 1
    if changed:
        await session.flush()
    return changed
