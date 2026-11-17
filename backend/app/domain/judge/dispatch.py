"""领走排着的评测，派到评测主机上跑，写回结果（方案 §一次评测怎么跑）。

照 ``task/starter_jobs.py`` 那套「表加租约」：一行一次评测，领的时候先把租约写下、
提交，再去干活，干活期间不占着事务；租约被别人接走的那个领取者写不回结果（也不扣钱）。

* 评测主机是 ``judge_device_ids`` 里此刻在线的设备，每台同时跑
  ``judge_slots_per_device`` 份；一次 tick 按空出来的位子领、一起跑，跑完再领下一批。
* 一次评测在主机上是 ``python3 -I -c <runner>``，任务从标准输入进去
  （``exec`` 最长 600 秒，方案「一次评测总时长 10 分钟」）。
* 没配评测主机、没设评测价格：排着的评测直接标失败，文案说「评测暂不可用」，不报错、
  不扣钱。配了主机但一台都不在线，就排着等；排过 ``QUEUE_LIMIT`` 还没轮上，标失败。
* 平台的原因（主机掉线、超时、docker 起不来、托管方没答上来）重试一次，再失败就标
  失败，文案 ``judgePlatformFailed`` —— 学生不扣分：那一行测试分空着（``auto_score``
  记 pending），老师可以手填。
* 不是平台的原因（GitHub 项目、代码太大、空间没有所有者、所有者没额度、形态暂不支持）
  当场标失败，带那句话，不重试。
* 跑出来了就是「完成」，编译失败、全错也是；结果和那一笔算力在同一个事务里写下。

设备连接每次现取（``agent.device_hub.device_hub``），测试换成假的 hub 就行。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.device_failures import (
    DeviceCallError,
    DeviceNotReady,
    DeviceOffline,
    LinkInterrupted,
)
from app.core.errors import BaseError
from app.core.sentences import NoticeText, say
from app.domain.judge import billing, code, trials
from app.domain.judge.runtimes import image_of
from app.domain.judge.script import RUNNER_SRC
from app.domain.task.judge_queue import DONE, FAILED, QUEUED, RUNNING
from app.domain.task.judging_models import TaskJudgeRun, TaskTestSuite
from app.domain.task.models import Task
from app.domain.task.test_suite import cases_with_text
from app.domain.usage.compute import ComputeRefused

log = logging.getLogger(__name__)

#: Longer than one judging can take: the host's 600 s and fetching the code.
LEASE = timedelta(minutes=15)
EXEC_TIMEOUT_S = 600
#: A judging that failed for the platform's reason is tried this many times.
MAX_ATTEMPTS = 2
RETRY_AFTER = timedelta(seconds=30)
#: A run still waiting for a judge host this long after it was queued fails.
QUEUE_LIMIT = timedelta(hours=1)
#: Inputs and expected outputs kept on a result row for the graders' view.
KEPT_TEXT = 4096

#: Told once a judging of a submission is settled (done or failed), with its
#: run id, so the student hears the result.
Judged = Callable[[int], Awaitable[None]]
#: The code of a project at a commit, as a tar (``code.fetch_archive``).
FetchCode = Callable[[AsyncSession, uuid.UUID, str], Awaitable[bytes]]


class _Refused(Exception):
    """Fails the judging now, with this sentence; trying again changes nothing."""

    def __init__(self, message: NoticeText) -> None:
        super().__init__(str(message))
        self.message = message


class _Platform(Exception):
    """The platform could not judge it this time; tried again."""


def _hub():
    # deferred-import: the hub singleton is read at call time, so a test that
    # replaces ``device_hub.device_hub`` is the hub every judging talks to.
    from app.domain.agent import device_hub

    return device_hub.device_hub


def _unavailable() -> NoticeText | None:
    """Why no judging can run on this deployment at all, or None."""
    if not settings.judge_device_ids:
        return say("judgeUnavailable")
    if settings.judge_credits_per_minute is None:
        return say("judgeUnpriced")
    return None


async def _fail_waiting(
    sessions: SessionFactory, message: NoticeText, *, older_than: datetime | None
) -> list[int]:
    """Fail every waiting run nobody holds — queued, or left running by a
    worker whose lease ran out — (only those queued before ``older_than``,
    when given) with ``message``. Returns the submission runs failed."""
    now = datetime.now(UTC)
    conditions = [
        TaskJudgeRun.state.in_((QUEUED, RUNNING)),
        or_(TaskJudgeRun.lease_until.is_(None), TaskJudgeRun.lease_until <= now),
    ]
    if older_than is not None:
        conditions.append(TaskJudgeRun.created_at < older_than)
    async with sessions() as session:
        rows = await session.execute(
            update(TaskJudgeRun)
            .where(*conditions)
            .values(
                state=FAILED,
                error=message.descriptor(),
                finished_at=now,
                lease_until=None,
            )
            .returning(TaskJudgeRun.id, TaskJudgeRun.purpose)
        )
        failed = [run_id for run_id, purpose in rows if purpose == "SUBMISSION"]
        await session.commit()
    return failed


async def _free_slots(sessions: SessionFactory) -> list[str]:
    """One entry per judging the online judge hosts can take now."""
    hub = _hub()
    online = [d for d in dict.fromkeys(settings.judge_device_ids) if hub.is_online(d)]
    if not online:
        return []
    now = datetime.now(UTC)
    async with sessions() as session:
        rows = await session.execute(
            select(TaskJudgeRun.device_id, func.count())
            .where(
                TaskJudgeRun.state == RUNNING,
                TaskJudgeRun.lease_until > now,
                TaskJudgeRun.device_id.in_(online),
            )
            .group_by(TaskJudgeRun.device_id)
        )
        busy: dict[str, int] = {str(d): int(n) for d, n in rows.all()}
    slots: list[str] = []
    for device in online:
        slots += [device] * max(
            0, settings.judge_slots_per_device - busy.get(device, 0)
        )
    return slots


async def claim_next(
    sessions: SessionFactory, device_id: str
) -> tuple[int, uuid.UUID] | None:
    """Lease the oldest run whose turn has come for ``device_id``: a queued one
    past its retry time, or a running one whose lease ran out. Committed
    before it returns, so no other worker can take the same run."""
    stamp = datetime.now(UTC)
    async with sessions() as session:
        row = await session.scalar(
            select(TaskJudgeRun)
            .where(
                TaskJudgeRun.state.in_((QUEUED, RUNNING)),
                or_(
                    TaskJudgeRun.lease_until.is_(None),
                    TaskJudgeRun.lease_until <= stamp,
                ),
                or_(TaskJudgeRun.retry_at.is_(None), TaskJudgeRun.retry_at <= stamp),
            )
            .order_by(TaskJudgeRun.created_at, TaskJudgeRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return None
        token = uuid.uuid4()
        row.state = RUNNING
        row.attempts += 1
        row.claim_token = token
        row.lease_until = stamp + LEASE
        row.device_id = device_id
        row.started_at = row.started_at or stamp
        leased = (row.id, token)
        await session.commit()
    return leased


@dataclass
class _Job:
    """One judging as it is sent, and what is needed to record it."""

    payload: dict
    cases: list[dict]
    owner_id: int
    runtime: str
    suite_revision: int


async def _prepare(
    session: AsyncSession, run: TaskJudgeRun, fetch_code: FetchCode
) -> _Job:
    """Everything the judge host needs, after the payer is admitted."""
    trial_code: bytes | None = None
    if run.purpose == "TRIAL":
        found = await trials.material(run.trial_spec or {})
        suite, cases, space_id, revision = found.suite, found.cases, run.space_id, 0
        trial_code = found.code
    else:
        row = await session.get(TaskTestSuite, run.suite_id) if run.suite_id else None
        task = await session.get(Task, run.task_id) if run.task_id else None
        if row is None or task is None:
            raise _Refused(say("judgeSuiteGone"))
        suite = {
            "kind": row.kind,
            "runtime": row.runtime,
            "codeDir": row.code_dir,
            "compileCmd": row.compile_cmd,
            "runCmd": row.run_cmd,
            "timeLimitMs": row.time_limit_ms,
            "memoryMb": row.memory_mb,
            "compare": row.compare,
            "tolerance": row.tolerance,
        }
        cases = [
            {
                "index": case.index,
                "id": case.id,
                "name": case.name,
                "points": case.points,
                "visibility": case.visibility,
                "input": given,
                "expected": wanted,
            }
            for case, given, wanted in await cases_with_text(session, row.id)
        ]
        space_id, revision = task.space_id, row.revision

    owner_id = await billing.space_owner(session, space_id)
    if owner_id is None:
        raise _Refused(say("judgeNoPayer"))
    try:
        await billing.admit(session, await billing.payer_for_space(session, owner_id))
    except ComputeRefused as exc:
        message = exc.args[0] if exc.args else say("judgeUnpriced")
        raise _Refused(
            message if isinstance(message, NoticeText) else say("judgeUnpriced")
        ) from exc

    image = image_of(str(suite.get("runtime") or ""))
    if image is None:
        raise _Refused(say("judgeKindUnsupported"))
    if trial_code is not None:
        archive = trial_code
    else:
        assert run.project_id is not None
        try:
            archive = await fetch_code(session, run.project_id, run.code_sha)
        except code.CodeRefused as exc:
            raise _Refused(exc.message) from exc
        except BaseError as exc:
            why = str(exc)
            raise _Platform(why) from exc
    payload = {
        **suite,
        "image": image,
        "code": base64.b64encode(archive).decode(),
        "cases": [
            {"index": c["index"], "input": c["input"], "expected": c["expected"]}
            for c in cases
        ],
    }
    return _Job(
        payload=payload,
        cases=cases,
        owner_id=owner_id,
        runtime=str(suite.get("runtime") or ""),
        suite_revision=revision,
    )


async def _run_on_host(device_id: str, job: _Job) -> dict:
    """The runner's reply from ``device_id``. Raises ``_Platform`` for every
    way the host could not answer."""
    try:
        done = await _hub().exec(
            device_id,
            ["python3", "-I", "-c", RUNNER_SRC],
            stdin=json.dumps(job.payload),
            timeout=EXEC_TIMEOUT_S,
        )
    except (
        DeviceOffline,
        DeviceNotReady,
        DeviceCallError,
        LinkInterrupted,
        TimeoutError,
    ) as exc:
        why = f"{type(exc).__name__}: {exc}"
        raise _Platform(why) from exc
    try:
        reply = json.loads(str(done.get("stdout") or ""))
    except ValueError:
        reply = None
    if not isinstance(reply, dict):
        stderr = str(done.get("stderr") or "")[-500:]
        why = f"runner exited {done.get('exit')}: {stderr}"
        raise _Platform(why)
    if reply.get("ok"):
        return reply
    if reply.get("error") == "unsupported":
        raise _Refused(say("judgeKindUnsupported"))
    why = str(reply.get("detail") or reply.get("error") or "")[:500]
    raise _Platform(why)


def _results(job: _Job, reply: dict) -> tuple[list[dict], int, int]:
    """The result rows: each case as the setter wrote it, with what it did."""
    did = {
        entry.get("index"): entry
        for entry in reply.get("cases") or []
        if isinstance(entry, dict)
    }
    rows: list[dict] = []
    passed = total = 0
    for case in job.cases:
        ran = did.get(case["index"]) or {}
        status = str(ran.get("status") or "runtime_error")
        total += case["points"]
        passed += case["points"] if status == "ok" else 0
        row = {
            "index": case["index"],
            "caseId": case["id"],
            "name": case["name"],
            "visibility": case["visibility"],
            "points": case["points"],
            "status": status,
            "ms": ran.get("ms"),
            "memoryKb": ran.get("memoryKb"),
            "input": case["input"][:KEPT_TEXT],
            "expected": case["expected"][:KEPT_TEXT],
        }
        if ran.get("actual") is not None:
            row["actual"] = ran["actual"]
        if ran.get("stderr"):
            row["stderr"] = ran["stderr"]
        rows.append(row)
    return rows, passed, total


@dataclass
class _Outcome:
    reply: dict | None = None
    job: _Job | None = None
    refused: NoticeText | None = None
    platform: str | None = None


async def _record(
    sessions: SessionFactory, run_id: int, token: uuid.UUID, outcome: _Outcome
) -> tuple[str, str]:
    """Write what happened, if this worker still holds the lease. Returns
    (what happened, the run's purpose)."""
    async with sessions() as session:
        run = await session.scalar(
            select(TaskJudgeRun)
            .where(
                TaskJudgeRun.id == run_id,
                TaskJudgeRun.claim_token == token,
                TaskJudgeRun.state == RUNNING,
            )
            .with_for_update()
        )
        if run is None:
            return "lost", ""
        purpose = run.purpose
        now = datetime.now(UTC)
        run.lease_until = None
        if outcome.reply is not None and outcome.job is not None:
            job, reply = outcome.job, outcome.reply
            rows, passed, total = _results(job, reply)
            compiled = reply.get("compile") or {}
            run.state = DONE
            run.error = None
            run.results = rows
            run.passed_points = passed
            run.total_points = total
            run.suite_revision = job.suite_revision
            run.compile_output = (
                str(compiled.get("output") or "")[:KEPT_TEXT]
                if compiled.get("status") not in (None, "ok")
                else None
            )
            machine_ms = int(reply.get("machineMs") or 0)
            run.machine_ms = machine_ms
            run.finished_at = now
            run.credits = await billing.charge(
                session,
                await billing.payer_for_space(session, job.owner_id),
                owner_id=job.owner_id,
                machine_ms=machine_ms,
                runtime=job.runtime,
            )
            result = "done"
        elif outcome.refused is not None:
            run.state = FAILED
            run.error = outcome.refused.descriptor()
            run.finished_at = now
            result = "failed"
        elif run.attempts >= MAX_ATTEMPTS:
            log.warning("judge run %s failed again: %s", run_id, outcome.platform)
            run.state = FAILED
            run.error = say("judgePlatformFailed").descriptor()
            run.finished_at = now
            result = "failed"
        else:
            log.warning("judge run %s will be retried: %s", run_id, outcome.platform)
            run.state = QUEUED
            run.claim_token = None
            run.retry_at = now + RETRY_AFTER
            result = "retried"
        await session.commit()
    return result, purpose


async def _judge_one(
    sessions: SessionFactory,
    device_id: str,
    run_id: int,
    token: uuid.UUID,
    fetch_code: FetchCode,
) -> tuple[str, str]:
    outcome = _Outcome()
    try:
        async with sessions() as session:
            run = await session.get(TaskJudgeRun, run_id)
            assert run is not None  # leased just now
            job = await _prepare(session, run, fetch_code)
        outcome.job = job
        outcome.reply = await _run_on_host(device_id, job)
    except _Refused as exc:
        outcome.refused = exc.message
    except _Platform as exc:
        outcome.platform = str(exc)
    except Exception as exc:  # a crash is a retry, and logged
        log.exception("judge run %s failed", run_id)
        outcome.platform = f"{type(exc).__name__}: {exc}"
    if outcome.refused is not None or outcome.platform is not None:
        outcome.reply = None
    return await _record(sessions, run_id, token, outcome)


async def run_due(
    sessions: SessionFactory,
    *,
    judged: Judged | None = None,
    fetch_code: FetchCode = code.fetch_archive,
) -> dict[str, int]:
    """Judge what the judge hosts have room for now; fail what cannot run."""
    counts = {"done": 0, "failed": 0, "retried": 0, "lost": 0}
    settled: list[int] = []
    unavailable = _unavailable()
    if unavailable is not None:
        settled += await _fail_waiting(sessions, unavailable, older_than=None)
        counts["failed"] += len(settled)
    else:
        leased: list[tuple[str, int, uuid.UUID]] = []
        for device_id in await _free_slots(sessions):
            claim = await claim_next(sessions, device_id)
            if claim is None:
                break
            leased.append((device_id, *claim))
        outcomes = await asyncio.gather(
            *(
                _judge_one(sessions, device_id, run_id, token, fetch_code)
                for device_id, run_id, token in leased
            )
        )
        for (_, run_id, _), (result, purpose) in zip(leased, outcomes, strict=True):
            counts[result] += 1
            if result in ("done", "failed") and purpose == "SUBMISSION":
                settled.append(run_id)
        stale = await _fail_waiting(
            sessions,
            say("judgeQueueTimedOut"),
            older_than=datetime.now(UTC) - QUEUE_LIMIT,
        )
        counts["failed"] += len(stale)
        settled += stale
    if judged is not None:
        for run_id in settled:
            try:
                await judged(run_id)
            except Exception:  # telling the student must not undo the result
                log.exception("telling about judge run %s failed", run_id)
    return counts
