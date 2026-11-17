"""读一版提交的评测结果，按看的人裁剪（方案 §看结果、§数据结构）。

学生交了一版、这一阶段挂着测试点、也记下了代码版本时，这里排一行
（``enqueue_for_submission``）；领走它、派到评测主机上跑、写回结果的是
``app.domain.judge.dispatch``（照 ``task/starter_jobs.py`` 那套「表加租约」）——
题目这一层够不着设备，所以只排队、只读结果。学生页、老师批改窗口和 ``grading`` 都从
这里读同一份。

裁剪的规矩（方案 §安全、§看结果）：

* 谁都能看到**状态**和**通过几个 / 一共几个**（正在跑、失败也要能说）。
* 逐个用例的结果只给学生「过没过」和它花的时间；隐藏用例的输入、期望输出、实际
  输出学生看不到 —— 那些字节从来没进过学生项目，自然也不进他读取的响应。
* 能批改的人（出题人、空间管理员）看到全部用例的输入、期望和实际输出。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.task.judging_models import TaskJudgeRun
from app.domain.task.test_suite import suite_of_stage

#: 评测记录的状态，和 ``TaskJudgeRun.state`` 的 CHECK 一致。
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
#: 还没跑完的状态，页面上显示「评测中」。
IN_FLIGHT = (QUEUED, RUNNING)


async def latest_run(session: AsyncSession, submission_id: int) -> TaskJudgeRun | None:
    """这一版提交最近一次评测，没有就 None。改题重评会写新的一行，读的一直是
    最新的那一次。"""
    if submission_id <= 0:
        return None
    return await session.scalar(
        select(TaskJudgeRun)
        .where(TaskJudgeRun.submission_id == submission_id)
        .order_by(TaskJudgeRun.created_at.desc(), TaskJudgeRun.id.desc())
        .limit(1)
    )


async def latest_runs(
    session: AsyncSession, submission_ids: Sequence[int]
) -> dict[int, TaskJudgeRun]:
    """多版提交各自最近的一次评测，按提交 id 归位。``latest_run`` 的批量版本，
    条件与它逐字一致；没有评测的提交不在结果里。"""
    ids = [i for i in dict.fromkeys(submission_ids) if i > 0]
    if not ids:
        return {}
    rows = await session.scalars(
        select(TaskJudgeRun)
        .where(TaskJudgeRun.submission_id.in_(ids))
        .order_by(TaskJudgeRun.created_at.desc(), TaskJudgeRun.id.desc())
    )
    first: dict[int, TaskJudgeRun] = {}
    for row in rows:
        if row.submission_id is not None:
            first.setdefault(row.submission_id, row)
    return first


async def enqueue_for_submission(
    session: AsyncSession,
    *,
    task_id: int,
    stage: int,
    submission_id: int,
    project_id: uuid.UUID | None,
    code_sha: str,
    requested_by: int,
) -> TaskJudgeRun | None:
    """Queue the judging of a version just handed in; None when there is
    nothing to judge — the stage carries no 测试点, or no code version was
    recorded (a GitHub project, a forge that did not answer).

    The same code judged by the same revision of the same suite has one
    answer, so a version whose commit was already judged done gets that
    result again, with no second run and no second charge (方案 §控制用量).
    """
    suite = await suite_of_stage(session, task_id, stage)
    if suite is None or not code_sha or project_id is None:
        return None
    now = datetime.now(UTC)
    run = TaskJudgeRun(
        task_id=task_id,
        suite_id=suite.id,
        suite_revision=suite.revision,
        submission_id=submission_id,
        purpose="SUBMISSION",
        project_id=project_id,
        code_sha=code_sha,
        requested_by=requested_by,
        state=QUEUED,
        created_at=now,
    )
    done = await session.scalar(
        select(TaskJudgeRun)
        .where(
            TaskJudgeRun.suite_id == suite.id,
            TaskJudgeRun.suite_revision == suite.revision,
            TaskJudgeRun.code_sha == code_sha,
            TaskJudgeRun.state == DONE,
        )
        .order_by(TaskJudgeRun.finished_at.desc())
        .limit(1)
    )
    if done is not None:
        run.state = DONE
        run.started_at = done.started_at
        run.finished_at = now
        run.machine_ms = done.machine_ms
        run.compile_output = done.compile_output
        run.results = done.results
        run.passed_points = done.passed_points
        run.total_points = done.total_points
        run.device_id = done.device_id
    session.add(run)
    await session.flush()
    return run


def _points(run: TaskJudgeRun) -> tuple[int, int]:
    return int(run.passed_points or 0), int(run.total_points or 0)


def run_to_api(run: TaskJudgeRun | None, *, teacher: bool) -> dict | None:
    """一次评测送到接口上的形状，按看的人裁剪。

    出题人（``teacher`` 为真）读到每个用例的输入、期望和实际输出；别人读到的是
    名称、过没过和时间 —— 隐藏用例的字节不进这份响应（方案 §隐藏用例只看通过与否，
    也 §老师能展开隐藏用例的输入、期望和实际输出；学生那边看不到）。
    """
    if run is None:
        return None
    passed, total = _points(run)
    out: dict = {
        "state": run.state,
        "purpose": run.purpose,
        "codeSha": run.code_sha,
        "suiteRevision": run.suite_revision,
        "passedPoints": passed,
        "totalPoints": total,
        "createdAt": int(run.created_at.timestamp() * 1000),
        "startedAt": _ms(run.started_at),
        "finishedAt": _ms(run.finished_at),
        "machineMs": run.machine_ms,
    }
    # 还没跑完的那几次只报状态，不报用例（还没有结果）。
    if run.state in IN_FLIGHT:
        return out
    if run.error:
        out["error"] = run.error
    if run.compile_output and (teacher or (run.results is None)):
        out["compileOutput"] = run.compile_output
    cases = [_case_to_api(entry, teacher=teacher) for entry in run.results or []]
    out["cases"] = [c for c in cases if c is not None]
    out["passedCases"] = sum(1 for c in out["cases"] if c.get("status") == "ok")
    out["caseCount"] = len(out["cases"])
    return out


def _case_to_api(entry: object, *, teacher: bool) -> dict | None:
    if not isinstance(entry, dict):
        return None
    out: dict = {
        "name": str(entry.get("name") or ""),
        "visibility": str(entry.get("visibility") or "SAMPLE"),
        "status": str(entry.get("status") or ""),
        "points": int(entry.get("points") or 0),
    }
    if entry.get("ms") is not None:
        out["ms"] = int(entry["ms"])
    if entry.get("memoryKb") is not None:
        out["memoryKb"] = int(entry["memoryKb"])
    # 只有能批改的人拿到输入、期望和实际输出；学生拿到的这一格不存在。
    if teacher:
        if entry.get("caseId") is not None:
            out["caseId"] = int(entry["caseId"])
        if entry.get("input") is not None:
            out["input"] = str(entry["input"])
        if entry.get("expected") is not None:
            out["expected"] = str(entry["expected"])
        if entry.get("actual") is not None:
            out["actual"] = str(entry["actual"])
        if entry.get("stderr"):
            out["stderr"] = str(entry["stderr"])
    elif str(entry.get("visibility")) == "SAMPLE":
        # 样例（可见）用例的实际输出和报错学生也能看到：输入输出本来就公开。
        if entry.get("actual") is not None:
            out["actual"] = str(entry["actual"])
        if entry.get("stderr"):
            out["stderr"] = str(entry["stderr"])
    return out


def _ms(moment) -> int | None:
    return int(moment.timestamp() * 1000) if moment is not None else None
