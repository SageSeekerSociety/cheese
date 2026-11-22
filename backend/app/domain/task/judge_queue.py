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
* 测试脚本的失败信息、命令判分的程序输出：老师看全文；学生看样例那几条测试的失败信息，
  和出题人允许的那几行输出（``student_output``）。score.json 里 ``visible: false`` 的
  那几项学生那边整行都没有。

**判分失败**（测试脚本没写出报告、判分命令没给出分）的那一次 ``state`` 是 ``done``、
带着 ``error``、两个分数为空：``scoreFailed`` 为真。它和还没跑完一样不计分
（``auto_score``），也不被「同一版代码已经评过」拿去复用。
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task.judging_models import TaskJudgeRun, TaskTestCase
from app.domain.task.models import TaskMembership, TaskSubmission
from app.domain.task.test_suite import IO, SAMPLE, suite_of_stage

#: 评测记录的状态，和 ``TaskJudgeRun.state`` 的 CHECK 一致。
QUEUED, RUNNING, DONE, FAILED = "queued", "running", "done", "failed"
#: 还没跑完的状态，页面上显示「评测中」。
IN_FLIGHT = (QUEUED, RUNNING)
#: 学生自测（``TaskJudgeRun.purpose``）。
SELF_TEST = "SELF_TEST"


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
    recorded (no repository, a forge that did not answer).

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
            # 判分失败的那次不是答案：再评一次也许就读得出分了。
            TaskJudgeRun.error.is_(None),
            TaskJudgeRun.purpose == "SUBMISSION",
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
        run.student_output = done.student_output
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

    跑完以后多出来的几格：``scoreFailed``（判分失败，见模块开头）、``compileOutput``
    （IO 的编译输出；测试脚本与命令判分的程序输出前 200 行，只给老师）、
    ``studentOutput``（出题人允许学生看的那几行，谁都看得到）。每个用例另有
    ``testId``、``message``（测试的失败信息）、``score``/``maxScore``（命令判分那一项的
    得分与满分，可以是小数）、``skipped``、``unscored``（报告里有、出题人没配分的测试）。
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
    out["scoreFailed"] = run.state == DONE and bool(run.error)
    if run.compile_output and (teacher or (run.results is None)):
        out["compileOutput"] = run.compile_output
    if run.student_output:
        out["studentOutput"] = run.student_output
    cases = [_case_to_api(entry, teacher=teacher) for entry in run.results or []]
    out["cases"] = [c for c in cases if c is not None]
    out["passedCases"] = sum(1 for c in out["cases"] if c.get("status") == "ok")
    out["caseCount"] = len(out["cases"])
    return out


def _case_to_api(entry: object, *, teacher: bool) -> dict | None:
    if not isinstance(entry, dict):
        return None
    if entry.get("private") and not teacher:
        # score.json 里 ``visible: false`` 的那一项：学生那边连这一行都没有。
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
    if entry.get("testId"):
        out["testId"] = str(entry["testId"])
    for key in ("score", "maxScore"):
        if isinstance(entry.get(key), int | float):
            out[key] = float(entry[key])
    for key in ("skipped", "unscored", "private"):
        if entry.get(key):
            out[key] = True
    # 测试的失败信息和用例的实际输出一个口径：老师都看，学生只看样例那几条。
    if entry.get("message") and (teacher or str(entry.get("visibility")) == "SAMPLE"):
        out["message"] = str(entry["message"])
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


def _today_utc(now: datetime) -> datetime:
    """自测次数按 UTC 的自然日数（同 ``day_windows``）：每天 0 点（UTC）清零。"""
    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _self_test_filter(task_id: int, membership_id: int, suite_id: int) -> list:
    return [
        TaskJudgeRun.task_id == task_id,
        TaskJudgeRun.suite_id == suite_id,
        TaskJudgeRun.purpose == SELF_TEST,
        TaskJudgeRun.trial_spec["participantId"].as_integer() == membership_id,
    ]


async def self_tests_today(
    session: AsyncSession, *, task_id: int, membership_id: int, suite_id: int
) -> int:
    """这个参与者今天（UTC）在这一套测试上自测了几次，失败的也算：排上了就占一次。"""
    since = _today_utc(datetime.now(UTC))
    count = await session.scalar(
        select(func.count())
        .select_from(TaskJudgeRun)
        .where(
            *_self_test_filter(task_id, membership_id, suite_id),
            TaskJudgeRun.created_at >= since,
        )
    )
    return int(count or 0)


async def latest_self_test(
    session: AsyncSession, *, task_id: int, membership_id: int, suite_id: int
) -> TaskJudgeRun | None:
    """这个参与者在这一套测试上最近一次自测，没有就 None。"""
    return await session.scalar(
        select(TaskJudgeRun)
        .where(*_self_test_filter(task_id, membership_id, suite_id))
        .order_by(TaskJudgeRun.created_at.desc(), TaskJudgeRun.id.desc())
        .limit(1)
    )


def next_reset(now: datetime | None = None) -> datetime:
    """自测次数下一次清零的时刻（下一个 UTC 0 点）。"""
    return _today_utc(now or datetime.now(UTC)) + timedelta(days=1)


async def enqueue_self_test(
    session: AsyncSession,
    *,
    task_id: int,
    stage: int,
    membership_id: int,
    project_id: uuid.UUID | None,
    code_sha: str,
    requested_by: int,
) -> TaskJudgeRun:
    """排一次自测（方案 §自测样例）：拿默认分支现在的代码只跑这一阶段的样例用例。

    不挂在哪一版提交上、不计分，也不复用「同一版代码已经评过」的结果（学生要的就是
    「现在这一版」跑一遍）。这一步只有 ``IO`` 测试套能自测。拒的几种（一句话说清）：
    这一阶段没有测试点、不是输入/输出用例、出题人没开自测、没有样例用例、今天的次数
    用完了、读不到代码版本。截止时间那几条由路由先挡（和交作业同一道门）。
    """
    suite = await suite_of_stage(session, task_id, stage)
    if suite is None:
        raise BadRequestError(say("judgeSelfTestNoSuite"))
    if suite.kind != IO:
        raise BadRequestError(say("judgeSelfTestIoOnly"))
    if suite.self_test_daily is None:
        raise BadRequestError(say("judgeSelfTestOff"))
    samples = await session.scalar(
        select(func.count())
        .select_from(TaskTestCase)
        .where(
            TaskTestCase.suite_id == suite.id,
            TaskTestCase.superseded_at.is_(None),
            TaskTestCase.visibility == SAMPLE,
        )
    )
    if not samples:
        raise BadRequestError(say("judgeSelfTestNoSamples"))
    used = await self_tests_today(
        session, task_id=task_id, membership_id=membership_id, suite_id=suite.id
    )
    if used >= suite.self_test_daily:
        raise BadRequestError(say("judgeSelfTestLimit", max=suite.self_test_daily))
    if not code_sha or project_id is None:
        raise BadRequestError(say("judgeSelfTestNoCode"))
    run = TaskJudgeRun(
        task_id=task_id,
        suite_id=suite.id,
        suite_revision=suite.revision,
        purpose=SELF_TEST,
        trial_spec={"participantId": membership_id, "stage": stage},
        project_id=project_id,
        code_sha=code_sha,
        requested_by=requested_by,
        state=QUEUED,
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    return run


async def latest_submissions(
    session: AsyncSession, task_id: int, stage: int
) -> list[TaskSubmission]:
    """每个参与者在这一阶段最新的那一版提交（删掉的不算）。"""
    rows = await session.scalars(
        select(TaskSubmission)
        .join(TaskMembership, TaskSubmission.membership_id == TaskMembership.id)
        .where(
            TaskMembership.task_id == task_id,
            TaskSubmission.stage == stage,
            TaskSubmission.deleted_at.is_(None),
        )
        .order_by(
            TaskSubmission.membership_id,
            TaskSubmission.version.desc(),
            TaskSubmission.id.desc(),
        )
    )
    first: dict[int, TaskSubmission] = {}
    for row in rows:
        first.setdefault(row.membership_id, row)
    return list(first.values())


#: 重评一版提交时去哪找它的项目：之前没评测过的那几版（测试点是后来才加的）由路由按
#: 领题找回来。
ProjectFor = Callable[[TaskSubmission], Awaitable[uuid.UUID | None]]


async def rejudge_stage(
    session: AsyncSession,
    *,
    task_id: int,
    stage: int,
    requested_by: int,
    project_for: ProjectFor,
) -> dict[str, int]:
    """把这一阶段每个参与者最新的那一版提交，按**现在**这一版测试套再排一次评测
    （方案 §改题后重新评测）。

    走 ``enqueue_for_submission`` 那条路：同一份代码在这一版测试上已经评完的，直接
    拿那次的结果（``reused``），不再跑、不再扣钱。已经在这一版上评完（且没有判分失败）
    或正在评的那几版不动（``unchanged``）；没记下代码版本、找不到项目的跳过
    （``skipped``）。评审不动：老师已经批过的那一版，分数照旧是评审里记的那个。
    """
    counts = {"queued": 0, "reused": 0, "unchanged": 0, "skipped": 0}
    suite = await suite_of_stage(session, task_id, stage)
    if suite is None:
        return counts
    submissions = await latest_submissions(session, task_id, stage)
    previous = await latest_runs(session, [s.id for s in submissions])
    for submission in submissions:
        if not submission.code_sha:
            counts["skipped"] += 1
            continue
        prev = previous.get(submission.id)
        current = (
            prev is not None
            and prev.suite_id == suite.id
            and prev.suite_revision == suite.revision
        )
        if (
            prev is not None
            and current
            and (prev.state in IN_FLIGHT or (prev.state == DONE and not prev.error))
        ):
            counts["unchanged"] += 1
            continue
        project_id = prev.project_id if prev is not None else None
        if project_id is None:
            project_id = await project_for(submission)
        run = await enqueue_for_submission(
            session,
            task_id=task_id,
            stage=stage,
            submission_id=submission.id,
            project_id=project_id,
            code_sha=submission.code_sha,
            requested_by=requested_by,
        )
        if run is None:
            counts["skipped"] += 1
        else:
            counts["reused" if run.state == DONE else "queued"] += 1
    return counts
