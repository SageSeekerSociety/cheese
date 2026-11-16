"""自动行和老师手动分怎么合成一条评审的总分（方案 §评审那一行）。

评分要点里带 ``kind`` 的行由平台给分（``rubric.AUTOMATIC_KINDS``）：``usage`` 按领题
用掉的额度（``usage_score``），``tests`` 按这一阶段最新一次评测里通过用例的分值占比
（``grading.tests_line_score``）。含自动行的阶段合计 100（``rubric._check_automatic``），
所以总分就是老师填的手动分加上各自动行的分 —— 不再折算。

老师填的 ``score`` 是这一阶段**手动**几项的分（0 到这一阶段手动项分值和）。自动行算不
出来时给 0：``usage`` 行在领题没交作业时给 0；``tests`` 行在这一版还没有跑完的评测、
或者评测失败时给 0，并在那一行标 ``pending`` —— 老师可以用 ``testsOverride``（带原因）
手动给 tests 行分，改过的分记进那一行（``adjusted`` 为真、``reason`` 是原因）。

驳回不记（沿用主干）：``accepted`` 为假时 ``auto_scores`` 是空的、总分原样落库。一道题
这一阶段没有自动行时整条路也和以前一样。记录的形状沿用主干：``{manual, final,
lines[]}``，``lines`` 里每项一种 ``kind``；主干只写 ``usage``，这里多一种 ``tests``。
``final`` 沿用主干的意思，只管按排名给分的 ``usage`` 行（截止前暂定）；``tests`` 行没有
暂定这回事，它这一刻的分就定了。
"""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task import grading, judge_queue, usage_score
from app.domain.task.models import Task, TaskMembership, TaskRubricItem, TaskSubmission
from app.domain.task.rubric import AUTOMATIC_KINDS, TESTS, USAGE


def _tests_override(raw: object, points: int) -> tuple[int, str]:
    """``testsOverride``（``{score, reason}``）读成（分，原因）。

    手动给 tests 行分必须写明原因（学生看得到）；分要落在这一行的分值内。
    """
    entry = raw if isinstance(raw, dict) else {}
    reason = str(entry.get("reason") or "").strip()
    if not reason:
        raise BadRequestError(say("lineScoreNeedsReason"))
    value = entry.get("score")
    if value is None:
        raise BadRequestError(say("lineScoreRange", points=points))
    try:
        score = int(value)
    except (TypeError, ValueError):
        raise BadRequestError(say("lineScoreRange", points=points)) from None
    if not 0 <= score <= max(points, 0):
        raise BadRequestError(say("lineScoreRange", points=points))
    return score, reason


async def _stage_lines(
    session: AsyncSession, task_id: int, stage: int
) -> Sequence[TaskRubricItem]:
    """这一阶段保存着的评分要点，按 ``index`` 排（``index`` 是接口对位用的）。"""
    rows = await session.scalars(
        select(TaskRubricItem)
        .where(TaskRubricItem.task_id == task_id, TaskRubricItem.stage == stage)
        .order_by(TaskRubricItem.index)
    )
    return list(rows)


async def _usage_entry(
    session: AsyncSession, task: Task, membership: TaskMembership, line: TaskRubricItem
) -> tuple[dict, bool]:
    """``usage`` 行这一刻的分，和它定稿了没有。"""
    view = (await usage_score.usage_of_claims(session, task, [membership.id]))[
        membership.id
    ]
    auto = int(view.get("score") or 0)
    entry = {
        "index": line.index,
        "kind": USAGE,
        "points": line.points,
        "score": auto,
        "credits": view["credits"],
        "mode": view["line"]["mode"],
    }
    return entry, bool(view["final"])


async def _tests_entry(
    session: AsyncSession,
    submission_id: int,
    line: TaskRubricItem,
    override: object,
) -> tuple[dict, bool]:
    """``tests`` 行这一刻的分，和它定稿了没有。

    有跑完的评测就按通过用例的分值占比折算；没有（还没跑、或者失败）就给 0 并标
    ``pending``。``override`` 是老师手动给的分，改过就记下原因、这一行随即定稿。
    """
    run = await judge_queue.latest_run(session, submission_id)
    passed = total = 0
    run_id = None
    pending = True
    if run is not None and run.state == judge_queue.DONE:
        passed = int(run.passed_points or 0)
        total = int(run.total_points or 0)
        run_id = run.id
        pending = False
    entry: dict = {
        "index": line.index,
        "kind": TESTS,
        "points": line.points,
        "passedPoints": passed,
        "totalPoints": total,
        "runId": run_id,
    }
    if override is not None:
        score, reason = _tests_override(override, line.points)
        entry.update(score=score, adjusted=True, reason=reason)
        return entry, True
    auto = grading.tests_line_score(line.points, passed, total)
    entry.update(score=int(auto or 0), adjusted=False)
    if pending:
        entry["pending"] = True
    return entry, not pending


async def compose_review(
    session: AsyncSession,
    *,
    task: Task,
    membership: TaskMembership,
    submission_id: int,
    accepted: bool,
    score: int | None,
    tests_override: object = None,
) -> tuple[int, dict | None]:
    """一条评审要记下的分（``{manual, final, lines[]}``）和总分。

    ``score`` 是老师填的这一阶段手动几项的分（0 到手动项分值和）；总分 = 它 + 各自动
    行的分。驳回、或者这一阶段没有自动行时总分原样、``auto_scores`` 为空。
    """
    if not accepted:
        return int(score or 0), None
    stage = await session.scalar(
        select(TaskSubmission.stage).where(TaskSubmission.id == submission_id)
    )
    stage_idx = int(stage or 0)
    rows = await _stage_lines(session, task.id, stage_idx)
    automatic = [row for row in rows if (row.kind or "") in AUTOMATIC_KINDS]
    if not automatic:
        return int(score or 0), None
    manual_points = sum(
        row.points for row in rows if (row.kind or "") not in AUTOMATIC_KINDS
    )
    manual = int(score or 0)
    if not 0 <= manual <= manual_points:
        raise BadRequestError(say("reviewManualScoreRange", max=manual_points))

    entries: list[dict] = []
    # ``final`` 只由 ``usage`` 行决定：按排名给分的额度题，别人的名次要到题目截止
    # 才定，截止后的清扫（``usage_score.finalize_ranked_reviews``）才把暂定分换成
    # 定稿分。``tests`` 行没有「暂定」这回事 —— 它的分在批改这一刻就定下了，没出结果
    # 就是 0（那一行自己标 ``pending``），不拖着整条评审等谁。
    final = True
    tests_seen = False
    for line in automatic:
        if line.kind == USAGE:
            entry, line_final = await _usage_entry(session, task, membership, line)
            final = final and line_final
        else:
            # 一个阶段最多一行 tests（``rubric._check_automatic``）；override 只给它。
            entry, _ = await _tests_entry(
                session, submission_id, line, None if tests_seen else tests_override
            )
            tests_seen = True
        entries.append(entry)

    total = manual + sum(int(entry["score"]) for entry in entries)
    return total, {"manual": manual, "final": final, "lines": entries}
