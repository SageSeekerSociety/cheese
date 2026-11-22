"""一道题的测试点数据：出题人在「数据」页签看的那几块（方案 §第⑤步）。

看的是**每个参与者在这一阶段最新的那一版提交**最近一次评测 —— 和老师批改、参与者列表
「测试点」那一列读的是同一次（``judge_queue.latest_runs`` 的口径：同一版提交按
``created_at``、``id`` 取最新）。撤回了的领题、删掉的提交不算。

三样东西：

* ``case_rates`` —— 每个用例：跑完的几份里过了几份、没过的那几份最常见的是哪种状态
  （「18 人超时」）。只数 ``done`` 且没有判分失败的那几次；排队中、失败、判分失败的
  不进分母。最差的排在最前面。
* ``score_buckets`` —— 测试分（``passedPoints / totalPoints``）落在 0–9%、10–19%……
  90–100% 各几人，另数判分失败、评测失败、还在评、没有评测的几份。
* ``credits`` —— 这道题评测一共花了多少额度：学生提交的评测（含老师重评）和学生自测，
  按 ``task_id`` 记在这道题上的每一行。出题人在表单里的试跑不挂在题目上（``task_id``
  为空，只记 ``space_id``），不在这里。

读法有界：最新提交一条语句、它们的最新评测一条语句（只取几个数，不搬 ``results``），
逐个用例的统计在库里用 ``jsonb_array_elements`` 聚合成「用例 × 状态 → 份数」一条语句，
额度一条语句。不随参与者数多发查询。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import Float, and_, cast, column, func, select, true
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.judge_queue import DONE, FAILED, IN_FLIGHT, SELF_TEST
from app.domain.task.judging_models import TaskJudgeRun, TaskTestSuite
from app.domain.task.models import TaskMembership, TaskSubmission

#: 测试分分布分几档：每档 10 个百分点，满分落在最后一档。
BUCKETS = 10
#: 最多列几个用例：一套测试点很少过百，这是防一份失控的报告撑大响应。
MAX_CASES = 300
OK = "ok"


@dataclass(frozen=True)
class _LatestRun:
    run_id: int
    state: str
    score_failed: bool
    current: bool
    passed: int
    total: int


def _latest_submission_ids(task_id: int, stage: int):
    """每个参与者在这一阶段最新那一版提交的 id（子查询）。"""
    return (
        select(TaskSubmission.id.label("sid"))
        .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
        .where(
            TaskMembership.task_id == task_id,
            TaskMembership.live_claim,
            TaskSubmission.stage == stage,
            TaskSubmission.deleted_at.is_(None),
        )
        .distinct(TaskSubmission.membership_id)
        .order_by(
            TaskSubmission.membership_id,
            TaskSubmission.version.desc(),
            TaskSubmission.id.desc(),
        )
        .subquery()
    )


async def _latest_runs(
    session: AsyncSession, task_id: int, stage: int, suite: TaskTestSuite
) -> tuple[int, list[_LatestRun]]:
    """(最新提交几份, 它们各自最近一次评测)。"""
    latest = _latest_submission_ids(task_id, stage)
    submissions = int(
        await session.scalar(select(func.count()).select_from(latest)) or 0
    )
    rows = await session.execute(
        select(
            TaskJudgeRun.id,
            TaskJudgeRun.state,
            TaskJudgeRun.error.is_not(None),
            TaskJudgeRun.suite_id,
            TaskJudgeRun.suite_revision,
            TaskJudgeRun.passed_points,
            TaskJudgeRun.total_points,
        )
        .where(TaskJudgeRun.submission_id.in_(select(latest.c.sid)))
        .distinct(TaskJudgeRun.submission_id)
        .order_by(
            TaskJudgeRun.submission_id,
            TaskJudgeRun.created_at.desc(),
            TaskJudgeRun.id.desc(),
        )
    )
    runs = [
        _LatestRun(
            run_id=int(run_id),
            state=str(state),
            score_failed=state == DONE and bool(has_error),
            current=suite_id == suite.id and revision == suite.revision,
            passed=int(passed or 0),
            total=int(total or 0),
        )
        for run_id, state, has_error, suite_id, revision, passed, total in rows.all()
    ]
    return submissions, runs


def _judged(run: _LatestRun) -> bool:
    """这一次算数：跑完了、读得出分。"""
    return run.state == DONE and not run.score_failed


async def _case_rates(session: AsyncSession, run_ids: Sequence[int]) -> list[dict]:
    """每个用例在这几次评测里各种状态各几份，按用例名归在一起。"""
    if not run_ids:
        return []
    entry = (
        func.jsonb_array_elements(TaskJudgeRun.results)
        .table_valued(column("value", JSONB))
        .lateral()
    )
    name = entry.c.value["name"].astext
    visibility = entry.c.value["visibility"].astext
    status = entry.c.value["status"].astext
    rows = await session.execute(
        select(
            name,
            func.min(visibility),
            func.min(cast(entry.c.value["index"].astext, Float)),
            status,
            func.count(),
        )
        .select_from(TaskJudgeRun)
        .join(entry, true())
        .where(TaskJudgeRun.id.in_(list(run_ids)))
        .group_by(name, status)
    )
    cases: dict[str, dict] = {}
    for case_name, case_visibility, index, case_status, count in rows.all():
        key = str(case_name or "")
        row = cases.setdefault(
            key,
            {
                "name": key,
                "visibility": str(case_visibility or "SAMPLE"),
                "index": index,
                "judged": 0,
                "passed": 0,
                "failures": {},
            },
        )
        if index is not None and (row["index"] is None or index < row["index"]):
            row["index"] = index
        row["judged"] += int(count)
        if case_status == OK:
            row["passed"] += int(count)
        else:
            failures = row["failures"]
            label = str(case_status or "unknown")
            failures[label] = failures.get(label, 0) + int(count)
    out = []
    for row in cases.values():
        failures: dict[str, int] = row.pop("failures")
        top = (
            min(failures.items(), key=lambda kv: (-kv[1], kv[0])) if failures else None
        )
        judged = row["judged"]
        out.append(
            {
                "name": row["name"],
                "visibility": row["visibility"],
                "judged": judged,
                "passed": row["passed"],
                "rate": round(row["passed"] / judged, 4) if judged else None,
                "topFailure": {"status": top[0], "count": top[1]} if top else None,
                "_index": row["index"] if row["index"] is not None else 1e9,
            }
        )
    # 最差的在前；一样差的按出题时的顺序。
    out.sort(key=lambda c: (c["rate"] if c["rate"] is not None else 2, c["_index"]))
    for row in out:
        row.pop("_index")
    return out[:MAX_CASES]


def _bucket(passed: int, total: int) -> int:
    return min(BUCKETS - 1, (passed * BUCKETS) // total) if total > 0 else 0


async def credits_of_task(session: AsyncSession, task_id: int) -> dict:
    """这道题的评测一共花了多少额度和机器时间（见模块开头：不含试跑）。"""
    is_self_test = TaskJudgeRun.purpose == SELF_TEST
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(TaskJudgeRun.credits), 0),
                func.coalesce(func.sum(TaskJudgeRun.credits).filter(is_self_test), 0),
                func.coalesce(func.sum(TaskJudgeRun.machine_ms), 0),
                func.count(TaskJudgeRun.id).filter(TaskJudgeRun.credits > 0),
            ).where(TaskJudgeRun.task_id == task_id)
        )
    ).one()
    total, self_tests, machine_ms, charged = row
    return {
        "total": round(float(total), 4),
        "submissions": round(float(total) - float(self_tests), 4),
        "selfTests": round(float(self_tests), 4),
        "machineMs": int(machine_ms),
        "chargedRuns": int(charged),
    }


async def stage_stats(session: AsyncSession, task_id: int, stage: int) -> dict | None:
    """这一阶段的用例通过率和测试分分布；这一阶段没挂测试点时 None。"""
    suite = await session.scalar(
        select(TaskTestSuite).where(
            TaskTestSuite.task_id == task_id, TaskTestSuite.stage == stage
        )
    )
    if suite is None:
        return None
    submissions, runs = await _latest_runs(session, task_id, stage, suite)
    judged = [r for r in runs if _judged(r)]
    buckets = [0] * BUCKETS
    rates: list[float] = []
    for run in judged:
        if run.total > 0:
            buckets[_bucket(run.passed, run.total)] += 1
            rates.append(run.passed / run.total)
    return {
        "kind": suite.kind,
        "submissions": submissions,
        "judged": len(judged),
        "scoreFailed": sum(1 for r in runs if r.score_failed),
        "failed": sum(1 for r in runs if r.state == FAILED),
        "inFlight": sum(1 for r in runs if r.state in IN_FLIGHT),
        "notJudged": submissions - len(runs),
        # 算在里面、却是按旧版测试评的那几份：出题人改过测试点、还没重评。
        "outdated": sum(1 for r in judged if not r.current),
        "averageRate": round(sum(rates) / len(rates), 4) if rates else None,
        "buckets": buckets,
        "cases": await _case_rates(session, [r.run_id for r in judged]),
    }


async def pass_rates_of_tasks(
    session: AsyncSession, task_ids: Sequence[int]
) -> dict[int, tuple[float, int]]:
    """几道题各自的测试平均通过率（学情分析「题目难度」）：每个参与者在每个挂着测试点
    的阶段最新那一版提交，最近一次评测读得出分的那几份，``passed/total`` 的平均，和
    这样的份数。没有一份的题不在结果里。只有全班的数，不出现任何人。"""
    ids = [int(t) for t in dict.fromkeys(task_ids)]
    if not ids:
        return {}
    latest = (
        select(TaskSubmission.id.label("sid"), TaskMembership.task_id.label("tid"))
        .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
        .join(
            TaskTestSuite,
            and_(
                TaskTestSuite.task_id == TaskMembership.task_id,
                TaskTestSuite.stage == TaskSubmission.stage,
            ),
        )
        .where(
            TaskMembership.task_id.in_(ids),
            TaskMembership.live_claim,
            TaskSubmission.deleted_at.is_(None),
        )
        .distinct(TaskSubmission.membership_id, TaskSubmission.stage)
        .order_by(
            TaskSubmission.membership_id,
            TaskSubmission.stage,
            TaskSubmission.version.desc(),
            TaskSubmission.id.desc(),
        )
        .subquery()
    )
    runs = (
        select(
            latest.c.tid,
            TaskJudgeRun.state,
            TaskJudgeRun.error,
            TaskJudgeRun.passed_points,
            TaskJudgeRun.total_points,
        )
        .join(latest, latest.c.sid == TaskJudgeRun.submission_id)
        .distinct(TaskJudgeRun.submission_id)
        .order_by(
            TaskJudgeRun.submission_id,
            TaskJudgeRun.created_at.desc(),
            TaskJudgeRun.id.desc(),
        )
        .subquery()
    )
    rate = cast(runs.c.passed_points, Float) / cast(runs.c.total_points, Float)
    rows = await session.execute(
        select(runs.c.tid, func.avg(rate), func.count())
        .where(
            runs.c.state == DONE,
            runs.c.error.is_(None),
            runs.c.total_points > 0,
        )
        .group_by(runs.c.tid)
    )
    return {int(tid): (round(float(avg), 4), int(n)) for tid, avg, n in rows.all() if n}
