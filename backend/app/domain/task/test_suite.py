"""一道题一个阶段的那一套测试点（方案 §出题人：配测试点、§数据结构）。

三样东西在这里：

* **怎么读进来**：``suite_lines`` 把出题表单送来的 ``testSuite`` 校验成一组
  :class:`SuiteLine`（一个阶段一条）。第 1 步只收 ``IO``（输入/输出用例）这一种形态，
  ``SCRIPT``（出题人的测试文件）和 ``COMMAND``（项目自带判分程序）表结构已经留好
  （``judging_models.TaskTestSuite.kind``），但到这里就拒 —— 文案键
  ``testSuiteKindLater``，等第 2 步。
* **怎么落库**：``replace_suites`` 整份替换（和评分要点、提交表单一模一样）：同一
  个 ``(题目, 阶段)`` 的套还在，行不删，``revision`` 加一（评测记录记的是它跑的第几
  版，所以改题不会动已出的结果）；用例走「写新行、旧行标 ``superseded_at``」，因为
  已出的评测结果指着老行。返回值是「这次有没有改」，题目版本据此记一版
  ``changes["tests"]``。
* **怎么发出去**：``suites_to_api`` 按看的人裁剪 —— 隐藏用例的输入和期望输出只给
  能批改的人；学生拿到的是「这一条隐藏用例叫什么（出题人允许的话）、多少分、过没过」。

用例的字节**不进题目材料，也不进学生项目**：输入和期望输出都写进私有存储
（``app.core.blob_store``，键 ``.judge-blobs/...``），这一行只记 ``location``/
``key``/``bytes``。第 1 步只内联文本（单个 ≤64 KB），照样走这条路 —— 隐藏用例的期望
输出任何时候都不该出现在学生拿得到的地方。
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import blob_store
from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task.judging_models import TaskTestCase, TaskTestSuite
from app.domain.task.stages import stage_of, stage_ref

#: 运行环境。五种固定版本，第一版不支持出题人自带镜像（方案 §运行环境镜像）。
RUNTIME_KEYS = ("python3.12", "cpp17", "java21", "node22", "go1.23")
#: 形态。表结构三种都留好；第 1 步只放 ``IO`` 进来。
KINDS = ("IO", "SCRIPT", "COMMAND")
IO = "IO"
#: 输出比对四种（方案 §输入/输出用例）。
COMPARE = ("TRIM_TRAILING", "EXACT", "NUMERIC", "IGNORE_CASE")
#: 可见性。
SAMPLE, HIDDEN = "SAMPLE", "HIDDEN"

#: 一套测试最多几个用例；一个文本项最多多少字节（方案 §限制 4：内联 ≤64 KB）。
MAX_CASES = 200
MAX_NAME_CHARS = 64
MAX_INLINE_BYTES = 64 * 1024
MAX_CASE_POINTS = 1000
#: 每个用例的时限（毫秒）：0.5–10 秒（方案 §限额）。
MIN_TIME_LIMIT_MS, MAX_TIME_LIMIT_MS = 500, 10_000
#: 内存（MB）：64–1024。
MIN_MEMORY_MB, MAX_MEMORY_MB = 64, 1024
MAX_CODE_DIR_CHARS = 255
MAX_CMD_CHARS = 4000
MAX_SELF_TEST_DAILY = 500


@dataclass(frozen=True)
class CaseLine:
    """一个用例，按出题人送来的样子（输入与期望输出是内联文本）。"""

    name: str
    points: int
    visibility: str
    input: str
    #: 隐藏用例的这一项只留在平台这边（私有存储），学生拿不到。
    expected: str


@dataclass(frozen=True)
class SuiteLine:
    """一个阶段的一整套测试点，校验并修剪过。"""

    stage: int
    kind: str
    runtime: str
    code_dir: str
    compile_cmd: str
    run_cmd: str
    time_limit_ms: int
    memory_mb: int
    compare: str
    tolerance: float
    show_hidden_names: bool
    self_test_daily: int | None
    cases: tuple[CaseLine, ...] = ()


def _int(raw, *, key: str, low: int, high: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise BadRequestError(say(key, low=low, high=high)) from None
    if not low <= value <= high:
        raise BadRequestError(say(key, low=low, high=high))
    return value


def _case(entry: dict, index: int) -> CaseLine:
    name = str(entry.get("name") or "").strip() or f"用例 {index + 1}"
    if len(name) > MAX_NAME_CHARS:
        raise BadRequestError(say("testSuiteNameTooLong", max=MAX_NAME_CHARS))
    points = _int(
        entry.get("points") or 0,
        key="testSuitePointsRange",
        low=0,
        high=MAX_CASE_POINTS,
    )
    visibility = str(entry.get("visibility") or SAMPLE).strip().upper() or SAMPLE
    if visibility not in (SAMPLE, HIDDEN):
        raise BadRequestError(say("testSuiteVisibilityUnknown"))
    text_in = str(entry.get("input") or "")
    text_out = str(entry.get("expected") or "")
    for text in (text_in, text_out):
        if len(text.encode()) > MAX_INLINE_BYTES:
            raise BadRequestError(
                say("testSuiteCaseTooLarge", max=MAX_INLINE_BYTES // 1024)
            )
    return CaseLine(
        name=name,
        points=points,
        visibility=visibility,
        input=text_in,
        expected=text_out,
    )


def _suite(entry: dict) -> SuiteLine:
    kind = str(entry.get("kind") or IO).strip().upper() or IO
    if kind not in KINDS:
        raise BadRequestError(say("testSuiteKindUnknown"))
    if kind != IO:
        # ``SCRIPT`` and ``COMMAND`` land in step 2; the tables already hold
        # them, but nothing here knows how to run or score them yet.
        raise BadRequestError(say("testSuiteKindLater"))
    runtime = str(entry.get("runtime") or "").strip() or RUNTIME_KEYS[0]
    if runtime not in RUNTIME_KEYS:
        raise BadRequestError(say("testSuiteRuntimeUnknown"))
    run_cmd = str(entry.get("runCmd") or "").strip()
    if not run_cmd:
        raise BadRequestError(say("testSuiteNoRunCommand"))
    compile_cmd = str(entry.get("compileCmd") or "").strip()
    if len(run_cmd) > MAX_CMD_CHARS or len(compile_cmd) > MAX_CMD_CHARS:
        raise BadRequestError(say("testSuiteCommandTooLong", max=MAX_CMD_CHARS))
    code_dir = str(entry.get("codeDir") or "").strip()
    if len(code_dir) > MAX_CODE_DIR_CHARS:
        raise BadRequestError(say("testSuiteCodeDirTooLong", max=MAX_CODE_DIR_CHARS))
    compare = str(entry.get("compare") or COMPARE[0]).strip().upper() or COMPARE[0]
    if compare not in COMPARE:
        raise BadRequestError(say("testSuiteCompareUnknown"))
    try:
        tolerance = float(entry.get("tolerance") or 0.0)
    except (TypeError, ValueError):
        raise BadRequestError(say("testSuiteToleranceRange")) from None
    if not 0.0 <= tolerance <= 1.0:
        raise BadRequestError(say("testSuiteToleranceRange"))
    raw_cases = entry.get("cases")
    cases = tuple(
        _case(c, i)
        for i, c in enumerate(raw_cases if isinstance(raw_cases, list) else [])
        if isinstance(c, dict)
    )
    if not cases:
        raise BadRequestError(say("testSuiteNoCases"))
    if len(cases) > MAX_CASES:
        raise BadRequestError(say("testSuiteTooManyCases", max=MAX_CASES))
    self_test = entry.get("selfTestDaily")
    if self_test in (None, "", False):
        self_test_daily = None
    else:
        self_test_daily = _int(
            self_test, key="testSuiteSelfTestRange", low=1, high=MAX_SELF_TEST_DAILY
        )
    return SuiteLine(
        stage=stage_of(entry),
        kind=kind,
        runtime=runtime,
        code_dir=code_dir,
        compile_cmd=compile_cmd,
        run_cmd=run_cmd,
        time_limit_ms=_int(
            entry.get("timeLimitMs") or MIN_TIME_LIMIT_MS,
            key="testSuiteTimeLimitRange",
            low=MIN_TIME_LIMIT_MS,
            high=MAX_TIME_LIMIT_MS,
        ),
        memory_mb=_int(
            entry.get("memoryMb") or MIN_MEMORY_MB,
            key="testSuiteMemoryRange",
            low=MIN_MEMORY_MB,
            high=MAX_MEMORY_MB,
        ),
        compare=compare,
        tolerance=tolerance,
        show_hidden_names=bool(entry.get("showHiddenNames", True)),
        self_test_daily=self_test_daily,
        cases=cases,
    )


def suite_lines(raw: list[dict] | None) -> list[SuiteLine]:
    """The ``testSuite`` field as sent — one suite per stage — checked and
    trimmed. A stage named twice is refused: there is one suite per stage."""
    out: list[SuiteLine] = []
    seen: set[int] = set()
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        line = _suite(entry)
        if line.stage in seen:
            raise BadRequestError(say("testSuiteStageDuplicate"))
        seen.add(line.stage)
        out.append(line)
    return out


async def list_suites(session: AsyncSession, task_id: int) -> list[TaskTestSuite]:
    """The task's suites, by stage, oldest row first."""
    rows = await session.scalars(
        select(TaskTestSuite)
        .where(TaskTestSuite.task_id == task_id)
        .order_by(TaskTestSuite.stage)
    )
    return list(rows)


async def cases_of(
    session: AsyncSession, suite_ids: Sequence[int]
) -> dict[int, list[TaskTestCase]]:
    """Each suite's live cases, in order (``superseded_at`` is None)."""
    if not suite_ids:
        return {}
    rows = await session.scalars(
        select(TaskTestCase)
        .where(
            TaskTestCase.suite_id.in_(list(suite_ids)),
            TaskTestCase.superseded_at.is_(None),
        )
        .order_by(TaskTestCase.suite_id, TaskTestCase.index)
    )
    out: dict[int, list[TaskTestCase]] = {}
    for row in rows:
        out.setdefault(row.suite_id, []).append(row)
    return out


async def suite_stages(session: AsyncSession, task_id: int) -> list[int]:
    """The stages that carry 测试点 — what ``rubric.check_tests_lines`` reads."""
    rows = await session.scalars(
        select(TaskTestSuite.stage).where(TaskTestSuite.task_id == task_id)
    )
    return list(rows)


def _key(task_id: int, side: str) -> str:
    """The bytes' address in the private store. Dot-named, like the platform's
    other private trees, and unique per write: one case's input never lands on
    another's."""
    return f".judge-blobs/{task_id}/{uuid.uuid4()}/{side}"


def _as_text(value: object) -> str:
    return "" if value is None else str(value)


async def _cases_text(rows: Sequence[TaskTestCase]) -> dict[int, tuple[str, str]]:
    """Each case's (input, expected) read back out of the private store."""
    out: dict[int, tuple[str, str]] = {}
    for row in rows:
        sides = []
        for location, key in (
            (row.input_location, row.input_key),
            (row.expected_location, row.expected_key),
        ):
            if not location or not key:
                sides.append("")
                continue
            try:
                sides.append((await blob_store.store(location).get(key)).decode())
            except (LookupError, UnicodeDecodeError):
                sides.append("")
        out[row.id] = (sides[0], sides[1])
    return out


async def replace_suites(
    session: AsyncSession, task_id: int, lines: Sequence[SuiteLine]
) -> bool:
    """Make the task's suites be ``lines`` — one per stage, replaced as a whole.

    A stage that keeps its suite keeps its row (``id`` unchanged, so judge runs
    pointing at it stay), and ``revision`` goes up only when something about it
    actually changed; its cases are written as new rows and the old ones are
    marked ``superseded_at``. A stage whose suite left the form has its row
    (and cases) removed — nothing points at it in step 1, since no judge run is
    written yet; when runs exist, step 2 retires the row instead of dropping it.

    Returns whether anything changed, which is what the 题目 version records.
    """
    existing = {row.stage: row for row in await list_suites(session, task_id)}
    wanted = {line.stage: line for line in lines}
    changed = False

    for stage, row in existing.items():
        if stage not in wanted:
            await session.delete(row)

    now = datetime.now(UTC)
    for stage, line in wanted.items():
        row = existing.get(stage)
        if row is None:
            row = TaskTestSuite(task_id=task_id, stage=stage, revision=1)
            _apply(row, line)
            session.add(row)
            changed = True
        elif _suite_differs(row, line):
            _apply(row, line)
            row.revision += 1
            changed = True
        if row.id is None:
            await session.flush()
        if await _write_cases(session, row, line, now):
            changed = True

    await session.flush()
    return changed


def _suite_differs(row: TaskTestSuite, line: SuiteLine) -> bool:
    return (
        row.kind != line.kind
        or row.runtime != line.runtime
        or (row.code_dir or "") != line.code_dir
        or (row.compile_cmd or "") != line.compile_cmd
        or row.run_cmd != line.run_cmd
        or row.time_limit_ms != line.time_limit_ms
        or row.memory_mb != line.memory_mb
        or row.compare != line.compare
        or float(row.tolerance or 0.0) != line.tolerance
        or bool(row.show_hidden_names) != line.show_hidden_names
        or row.self_test_daily != line.self_test_daily
    )


def _apply(row: TaskTestSuite, line: SuiteLine) -> None:
    row.kind = line.kind
    row.runtime = line.runtime
    row.code_dir = line.code_dir
    row.compile_cmd = line.compile_cmd
    row.run_cmd = line.run_cmd
    row.time_limit_ms = line.time_limit_ms
    row.memory_mb = line.memory_mb
    row.compare = line.compare
    row.tolerance = line.tolerance
    row.show_hidden_names = line.show_hidden_names
    row.self_test_daily = line.self_test_daily


async def _write_cases(session, row: TaskTestSuite, line: SuiteLine, now) -> bool:
    """Write the suite's cases as new rows, retiring the old ones. Returns
    whether the cases differ from what the suite already had."""
    live = (await cases_of(session, [row.id] if row.id is not None else [])).get(
        row.id, []
    )
    texts = await _cases_text(live)
    same = len(live) == len(line.cases) and all(
        old.name == new.name
        and old.points == new.points
        and old.visibility == new.visibility
        and texts.get(old.id, ("", ""))[0] == new.input
        and texts.get(old.id, ("", ""))[1] == new.expected
        for old, new in zip(live, line.cases, strict=True)
    )
    if same:
        return False
    store = blob_store.for_new()
    for old in live:
        old.superseded_at = now
    for index, case in enumerate(line.cases):
        write_in = _key(row.task_id, "in")
        write_out = _key(row.task_id, "out")
        new_case = TaskTestCase(
            suite_id=row.id,
            index=index,
            name=case.name,
            points=case.points,
            visibility=case.visibility,
            input_location=store.location,
            input_key=write_in,
            input_bytes=len(case.input.encode()),
            expected_location=store.location,
            expected_key=write_out,
            expected_bytes=len(case.expected.encode()),
            created_at=now,
        )
        session.add(new_case)
        # 「先记后写」：字节写失败时这一行随事务回滚，不会留下一行指着不存在的字节。
        await store.put(write_in, case.input.encode())
        await store.put(write_out, case.expected.encode())
    return True


async def suites_to_api(
    session: AsyncSession,
    task_id: int,
    keys: Sequence[str],
    *,
    teacher: bool,
) -> list[dict]:
    """The suites as readers get them. ``teacher`` (out题人 or an admin of the
    board) reads every case's input and expected output; anyone else reads a
    hidden case as its name (or 「隐藏用例 N」 when the suite hides names),
    its points, and whether it passed — never its bytes."""
    suites = await list_suites(session, task_id)
    by_suite = await cases_of(session, [s.id for s in suites])
    out: list[dict] = []
    hidden_seen = 0
    for suite in suites:
        cases = by_suite.get(suite.id, [])
        texts = await _cases_text(cases)
        case_rows: list[dict] = []
        for case in cases:
            shown = teacher or case.visibility == SAMPLE
            entry = {
                "name": case.name,
                "points": case.points,
                "visibility": case.visibility,
            }
            if not shown:
                hidden_seen += 1
                if not suite.show_hidden_names:
                    entry["name"] = f"隐藏用例 {hidden_seen}"
            else:
                text_in, text_out = texts.get(case.id, ("", ""))
                entry["input"] = text_in
                entry["expected"] = text_out
            case_rows.append(entry)
        out.append(
            {
                "stage": stage_ref(suite.stage, keys),
                "kind": suite.kind,
                "runtime": suite.runtime,
                "codeDir": suite.code_dir,
                "compileCmd": suite.compile_cmd,
                "runCmd": suite.run_cmd,
                "timeLimitMs": suite.time_limit_ms,
                "memoryMb": suite.memory_mb,
                "compare": suite.compare,
                "tolerance": suite.tolerance,
                "revision": suite.revision,
                "showHiddenNames": suite.show_hidden_names,
                "selfTestDaily": suite.self_test_daily,
                "cases": case_rows,
                "caseCount": len(cases),
                "totalPoints": sum(c.points for c in cases),
            }
        )
    return out
