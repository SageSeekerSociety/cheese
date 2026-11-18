"""一道题一个阶段的那一套测试点（方案 §出题人：配测试点、§数据结构）。

三样东西在这里：

* **怎么读进来**：``suite_lines`` 把出题表单送来的 ``testSuite`` 校验成一组
  :class:`SuiteLine`（一个阶段一条）。三种形态：``IO``（输入/输出用例）、``SCRIPT``
  （出题人的测试文件 + 一条运行命令，按 JUnit XML 报告逐个测试给分）、``COMMAND``（按
  顺序跑几条命令，从 ``SCORE: x/y`` 那一行或仓库根的 score.json 读分）。后两种这一步只能
  离线跑：``installDeps`` 为真就拒（文案 ``testSuiteInstallDepsLater``），评测容器里
  没有网，装不了依赖。
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

import asyncio
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import blob_store
from app.core.errors import BadRequestError
from app.core.sentences import say
from app.domain.task import test_files
from app.domain.task.judging_models import TaskTestCase, TaskTestSuite
from app.domain.task.stages import stage_of, stage_ref
from app.domain.task.test_files import FileLine

#: 运行环境。五种固定版本，第一版不支持出题人自带镜像（方案 §运行环境镜像）。
RUNTIME_KEYS = ("python3.12", "cpp17", "java21", "node22", "go1.23")
#: 形态，见模块开头。
KINDS = ("IO", "SCRIPT", "COMMAND")
IO, SCRIPT, COMMAND = KINDS
#: ``COMMAND`` 形态的分从哪读：命令打出的最后一行 ``SCORE: x/y``，或仓库根的 score.json。
SCORE_SOURCES = ("LINE", "JSON")
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
#: ``SCRIPT``/``COMMAND`` 一整轮的时限（秒）：默认 120 / 300，30–600（一次评测在评测
#: 主机上最长 10 分钟，见 ``judge.dispatch.EXEC_TIMEOUT_S``）。
SCRIPT_TIME_DEFAULT_S, COMMAND_TIME_DEFAULT_S = 120, 300
MIN_TOTAL_TIME_S, MAX_TOTAL_TIME_S = 30, 600
#: ``SCRIPT``/``COMMAND`` 不填内存时给多少（MB）：测试框架自己就要吃几十上百 MB（JVM
#: 起步就两三百），``IO`` 那个 64 MB 的下限跑不起来。
SCRIPT_MEMORY_DEFAULT_MB = 512
#: ``COMMAND`` 最多几条命令。
MAX_COMMANDS = 10
#: 判分程序的输出最多给学生看几行（老师看前 200 行）。
MAX_STUDENT_LOG_LINES = 200
#: ``SCRIPT`` 测试名最长多少字（``TaskTestCase.test_id`` 那一列）。
MAX_TEST_ID_CHARS = 128


@dataclass(frozen=True)
class CaseLine:
    """一个用例，按出题人送来的样子（输入与期望输出是内联文本）。"""

    name: str
    points: int
    visibility: str
    input: str
    #: 隐藏用例的这一项只留在平台这边（私有存储），学生拿不到。
    expected: str
    #: ``SCRIPT`` 形态：这一条给分的测试叫什么（``classname::name`` 或只写 ``name``，
    #: 见 ``judge.scoring``）；``IO`` 为 None。
    test_id: str | None = None


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
    #: ``COMMAND``：按顺序跑的命令。
    commands: tuple[str, ...] = ()
    #: ``COMMAND``：``LINE`` 或 ``JSON``；其余形态为 None。
    score_source: str | None = None
    #: ``SCRIPT`` 的测试文件、``COMMAND`` 的判分文件与保护文件（``test_files``）。
    files: tuple[FileLine, ...] = ()
    install_deps: bool = False
    #: ``SCRIPT``/``COMMAND`` 一整轮的时限（秒）；``IO`` 为 None。
    total_time_limit_s: int | None = None
    student_log_lines: int = 0

    @property
    def protected_paths(self) -> list[str]:
        return [f.path for f in self.files if f.role == test_files.PROTECTED]


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


def _script_case(entry: dict, index: int) -> CaseLine:
    """``SCRIPT`` 的一条给分测试：测试名必填，名称默认就是测试名。"""
    test_id = str(entry.get("testId") or "").strip()
    if not test_id:
        raise BadRequestError(say("testSuiteTestIdMissing"))
    if len(test_id) > MAX_TEST_ID_CHARS:
        raise BadRequestError(say("testSuiteTestIdTooLong", max=MAX_TEST_ID_CHARS))
    base = _case(
        {**entry, "name": entry.get("name") or test_id[-MAX_NAME_CHARS:]}, index
    )
    return CaseLine(
        name=base.name,
        points=base.points,
        visibility=base.visibility,
        input="",
        expected="",
        test_id=test_id,
    )


def _total_time(entry: dict, default: int) -> int:
    return _int(
        entry.get("totalTimeLimitS") or default,
        key="testSuiteTotalTimeRange",
        low=MIN_TOTAL_TIME_S,
        high=MAX_TOTAL_TIME_S,
    )


def _student_log_lines(entry: dict) -> int:
    return _int(
        entry.get("studentLogLines") or 0,
        key="testSuiteLogLinesRange",
        low=0,
        high=MAX_STUDENT_LOG_LINES,
    )


def _script_fields(entry: dict) -> dict:
    """``SCRIPT``：测试文件（至少一份）、运行命令、可选的逐个测试分值。

    不配 ``cases`` 就是「每个测试一样重」：报告里每个测试 1 分；配了就只按配了的那几条
    给分，报告里多出来的测试 0 分，配了却没跑到的算没过（``judge.scoring``）。
    """
    files = test_files.file_lines(entry.get("testFiles"), test_files.TEST)
    if not files:
        raise BadRequestError(say("testSuiteNoTestFiles"))
    test_files.check_total(files)
    raw_cases = entry.get("cases")
    cases = tuple(
        _script_case(c, i)
        for i, c in enumerate(raw_cases if isinstance(raw_cases, list) else [])
        if isinstance(c, dict)
    )
    if len(cases) > MAX_CASES:
        raise BadRequestError(say("testSuiteTooManyCases", max=MAX_CASES))
    ids = [c.test_id for c in cases]
    if len(set(ids)) != len(ids):
        raise BadRequestError(say("testSuiteTestIdDuplicate"))
    return {
        "files": tuple(files),
        "cases": cases,
        "total_time_limit_s": _total_time(entry, SCRIPT_TIME_DEFAULT_S),
    }


def _command_fields(entry: dict) -> dict:
    """``COMMAND``：几条命令、分从哪读、判分文件和保护文件。"""
    raw = entry.get("commands")
    commands = tuple(
        text
        for text in (
            str(c or "").strip() for c in (raw if isinstance(raw, list) else [])
        )
        if text
    )
    if not commands:
        raise BadRequestError(say("testSuiteNoCommands"))
    if len(commands) > MAX_COMMANDS:
        raise BadRequestError(say("testSuiteTooManyCommands", max=MAX_COMMANDS))
    if any(len(c) > MAX_CMD_CHARS for c in commands):
        raise BadRequestError(say("testSuiteCommandTooLong", max=MAX_CMD_CHARS))
    source = str(entry.get("scoreSource") or SCORE_SOURCES[0]).strip().upper()
    if source not in SCORE_SOURCES:
        raise BadRequestError(say("testSuiteScoreSourceUnknown"))
    graders = test_files.file_lines(entry.get("testFiles"), test_files.GRADER)
    protected = test_files.file_lines(entry.get("protectedFiles"), test_files.PROTECTED)
    clash = {f.path for f in graders} & {f.path for f in protected}
    if clash:
        raise BadRequestError(say("testSuiteFileDuplicate", path=sorted(clash)[0][:80]))
    files = [*protected, *graders]
    test_files.check_total(files)
    return {
        "commands": commands,
        "score_source": source,
        "files": tuple(files),
        "total_time_limit_s": _total_time(entry, COMMAND_TIME_DEFAULT_S),
    }


def _io_fields(entry: dict) -> dict:
    """``IO``：运行命令、可选的编译命令、比对方式和用例。"""
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
    return {
        "compare": compare,
        "tolerance": tolerance,
        "cases": cases,
        "time_limit_ms": _int(
            entry.get("timeLimitMs") or MIN_TIME_LIMIT_MS,
            key="testSuiteTimeLimitRange",
            low=MIN_TIME_LIMIT_MS,
            high=MAX_TIME_LIMIT_MS,
        ),
    }


def _suite(entry: dict) -> SuiteLine:
    kind = str(entry.get("kind") or IO).strip().upper() or IO
    if kind not in KINDS:
        raise BadRequestError(say("testSuiteKindUnknown"))
    runtime = str(entry.get("runtime") or "").strip() or RUNTIME_KEYS[0]
    if runtime not in RUNTIME_KEYS:
        raise BadRequestError(say("testSuiteRuntimeUnknown"))
    install_deps = bool(entry.get("installDeps"))
    if install_deps:
        # 装依赖要联网，评测容器这一步一个网口都没有（``judge.container``）。
        raise BadRequestError(say("testSuiteInstallDepsLater"))
    run_cmd = str(entry.get("runCmd") or "").strip()
    if kind != COMMAND and not run_cmd:
        raise BadRequestError(say("testSuiteNoRunCommand"))
    if kind == COMMAND:
        run_cmd = ""
    compile_cmd = str(entry.get("compileCmd") or "").strip() if kind == IO else ""
    if len(run_cmd) > MAX_CMD_CHARS or len(compile_cmd) > MAX_CMD_CHARS:
        raise BadRequestError(say("testSuiteCommandTooLong", max=MAX_CMD_CHARS))
    code_dir = str(entry.get("codeDir") or "").strip()
    if len(code_dir) > MAX_CODE_DIR_CHARS:
        raise BadRequestError(say("testSuiteCodeDirTooLong", max=MAX_CODE_DIR_CHARS))
    self_test = entry.get("selfTestDaily")
    if self_test in (None, "", False):
        self_test_daily = None
    else:
        self_test_daily = _int(
            self_test, key="testSuiteSelfTestRange", low=1, high=MAX_SELF_TEST_DAILY
        )
    if kind == IO:
        fields = _io_fields(entry)
    elif kind == SCRIPT:
        fields = _script_fields(entry)
    else:
        fields = _command_fields(entry)
    if kind != IO:
        fields["student_log_lines"] = _student_log_lines(entry)
    return SuiteLine(
        stage=stage_of(entry),
        kind=kind,
        runtime=runtime,
        code_dir=code_dir,
        compile_cmd=compile_cmd,
        run_cmd=run_cmd,
        time_limit_ms=fields.pop("time_limit_ms", MIN_TIME_LIMIT_MS),
        memory_mb=_int(
            entry.get("memoryMb")
            or (MIN_MEMORY_MB if kind == IO else SCRIPT_MEMORY_DEFAULT_MB),
            key="testSuiteMemoryRange",
            low=MIN_MEMORY_MB,
            high=MAX_MEMORY_MB,
        ),
        compare=fields.pop("compare", COMPARE[0]),
        tolerance=fields.pop("tolerance", 0.0),
        show_hidden_names=bool(entry.get("showHiddenNames", True)),
        self_test_daily=self_test_daily,
        install_deps=install_deps,
        **fields,
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


async def suite_of_stage(
    session: AsyncSession, task_id: int, stage: int
) -> TaskTestSuite | None:
    """The suite stage ``stage`` of the task carries, or None."""
    return await session.scalar(
        select(TaskTestSuite).where(
            TaskTestSuite.task_id == task_id, TaskTestSuite.stage == stage
        )
    )


async def cases_with_text(
    session: AsyncSession, suite_id: int
) -> list[tuple[TaskTestCase, str, str]]:
    """The suite's live cases in order, each with its input and expected
    output read out of the private store — what a judging is given."""
    rows = (await cases_of(session, [suite_id])).get(suite_id, [])
    texts = await _cases_text(rows)
    return [(row, *texts.get(row.id, ("", ""))) for row in rows]


async def suite_stages(session: AsyncSession, task_id: int) -> list[int]:
    """The stages that carry 测试点 — what ``rubric.check_tests_lines`` reads."""
    rows = await session.scalars(
        select(TaskTestSuite.stage).where(TaskTestSuite.task_id == task_id)
    )
    return list(rows)


async def case_of_task(
    session: AsyncSession, task_id: int, case_id: int
) -> TaskTestCase | None:
    """The live case ``case_id`` of ``task_id``, or None.

    A case's own id addresses nothing on its own: it is read only along the
    task the suite it belongs to hangs off (``GET /tasks/{id}/judge/cases/{cid}``,
    the graders' window over one hidden case). A case of another task, or one
    ``superseded_at``, is not this task's case — the caller answers 404, the
    same 口径 as ``access.bind_review_path``.
    """
    if case_id <= 0:
        return None
    return await session.scalar(
        select(TaskTestCase)
        .join(TaskTestSuite, TaskTestCase.suite_id == TaskTestSuite.id)
        .where(
            TaskTestCase.id == case_id,
            TaskTestCase.superseded_at.is_(None),
            TaskTestSuite.task_id == task_id,
        )
    )


async def case_to_api(session: AsyncSession, case: TaskTestCase) -> dict:
    """One case with its input and expected output read out of the private
    store — the graders' view of a case (keys and files ``suites_to_api``
    would fold away for a student)."""
    text_in, text_out = (await _cases_text([case])).get(case.id, ("", ""))
    return {
        "id": case.id,
        "index": case.index,
        "name": case.name,
        "points": case.points,
        "visibility": case.visibility,
        "input": text_in,
        "expected": text_out,
    }


def _key(task_id: int, side: str) -> str:
    """The bytes' address in the private store. Dot-named, like the platform's
    other private trees, and unique per write: one case's input never lands on
    another's."""
    return f".judge-blobs/{task_id}/{uuid.uuid4()}/{side}"


def _as_text(value: object) -> str:
    return "" if value is None else str(value)


async def _read_side(location: str | None, key: str | None) -> str:
    """One stored side back as text; a blank address or missing bytes read ""."""
    if not location or not key:
        return ""
    try:
        return (await blob_store.store(location).get(key)).decode()
    except (LookupError, UnicodeDecodeError):
        return ""


async def _cases_text(rows: Sequence[TaskTestCase]) -> dict[int, tuple[str, str]]:
    """Each case's (input, expected) read back out of the private store.

    Every side is read at once (``asyncio.gather``): a suite can hold
    :data:`MAX_CASES` cases here and a read one at a time would go to the
    store two hundred times over.
    """
    sides = await asyncio.gather(
        *(
            _read_side(location, key)
            for row in rows
            for location, key in (
                (row.input_location, row.input_key),
                (row.expected_location, row.expected_key),
            )
        )
    )
    return {row.id: (sides[2 * i], sides[2 * i + 1]) for i, row in enumerate(rows)}


async def replace_suites(
    session: AsyncSession, task_id: int, lines: Sequence[SuiteLine]
) -> bool:
    """Make the task's suites be ``lines`` — one per stage, replaced as a whole.

    A stage that keeps its suite keeps its row (``id`` unchanged, so judge runs
    pointing at it stay), and ``revision`` goes up only when something about it
    actually changed; its cases are written as new rows and the old ones are
    marked ``superseded_at``. A stage whose suite left the form has its row
    (and cases) removed, and the judge runs made against it go with it
    (``ON DELETE CASCADE``): taking a stage's 测试点 away takes its results.

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
        files_changed = row is None or not test_files.same_files(
            row.test_files, line.files
        )
        if row is None:
            row = TaskTestSuite(task_id=task_id, stage=stage, revision=1)
            _apply(row, line)
            session.add(row)
            changed = True
        elif _suite_differs(row, line) or files_changed:
            _apply(row, line)
            row.revision += 1
            changed = True
        if files_changed:
            # 每份文件一个新键：旧版测试套、已出的评测指着的字节不被覆盖。
            row.test_files = (
                await test_files.store_files(f".judge-blobs/{task_id}", line.files)
                or None
            )
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
        or list(row.commands or []) != list(line.commands)
        or row.score_source != line.score_source
        or list(row.protected_paths or []) != line.protected_paths
        or bool(row.install_deps) != line.install_deps
        or row.total_time_limit_s != line.total_time_limit_s
        or int(row.student_log_lines or 0) != line.student_log_lines
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
    row.commands = list(line.commands) or None
    row.score_source = line.score_source
    row.protected_paths = line.protected_paths or None
    row.install_deps = line.install_deps
    row.total_time_limit_s = line.total_time_limit_s
    row.student_log_lines = line.student_log_lines


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
        and old.test_id == new.test_id
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
        new_case = TaskTestCase(
            suite_id=row.id,
            index=index,
            name=case.name,
            points=case.points,
            visibility=case.visibility,
            test_id=case.test_id,
            created_at=now,
        )
        session.add(new_case)
        if case.test_id is not None:
            # ``SCRIPT`` 的一条只有测试名和分值，没有输入输出要存。
            continue
        write_in = _key(row.task_id, "in")
        write_out = _key(row.task_id, "out")
        new_case.input_location = new_case.expected_location = store.location
        new_case.input_key, new_case.expected_key = write_in, write_out
        new_case.input_bytes = len(case.input.encode())
        new_case.expected_bytes = len(case.expected.encode())
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
    """The suites as readers get them.

    ``teacher`` (出题人 or an admin of the board) reads everything: every
    case's input and expected output, and for ``SCRIPT``/``COMMAND`` the
    commands and every file in full (``testFiles``, ``protectedFiles``:
    ``[{path, bytes, content | base64}]`` — sent back unchanged, that is "no
    change"). Anyone else reads a hidden case as its name (or 「隐藏用例 N」
    when the suite hides names), its points, and whether it passed — never
    its bytes; of a ``SCRIPT`` its test names (``testId``) and visibility,
    and of a ``COMMAND`` only which files are restored before judging
    (``protectedPaths``) — never a file's bytes, nor the commands.

    Fields by kind (all suites carry ``stage``, ``kind``, ``runtime``,
    ``codeDir``, ``memoryMb``, ``revision``, ``showHiddenNames``,
    ``selfTestDaily``, ``cases``, ``caseCount``, ``totalPoints``):

    * ``IO``: ``compileCmd``, ``runCmd``, ``timeLimitMs``, ``compare``,
      ``tolerance``; each case ``{name, points, visibility, input?, expected?}``.
    * ``SCRIPT``: ``runCmd``, ``totalTimeLimitS``, ``studentLogLines``,
      ``installDeps``, ``testFileCount``; teachers also ``testFiles``; each case
      ``{name, testId, points, visibility}`` (no cases = every test weighs the
      same).
    * ``COMMAND``: ``scoreSource`` (``LINE``/``JSON``), ``totalTimeLimitS``,
      ``studentLogLines``, ``installDeps``, ``protectedPaths``; teachers also
      ``commands``, ``testFiles`` (grader files) and ``protectedFiles``.
    """
    suites = await list_suites(session, task_id)
    by_suite = await cases_of(session, [s.id for s in suites])
    out: list[dict] = []
    hidden_seen = 0
    for suite in suites:
        cases = by_suite.get(suite.id, [])
        texts = await _cases_text(cases) if suite.kind == IO else {}
        case_rows: list[dict] = []
        for case in cases:
            shown = teacher or case.visibility == SAMPLE
            entry = {
                "name": case.name,
                "points": case.points,
                "visibility": case.visibility,
            }
            if case.test_id is not None:
                entry["testId"] = case.test_id
            if not shown:
                hidden_seen += 1
                if not suite.show_hidden_names:
                    entry["name"] = f"隐藏用例 {hidden_seen}"
                    entry.pop("testId", None)
            elif suite.kind == IO:
                text_in, text_out = texts.get(case.id, ("", ""))
                entry["input"] = text_in
                entry["expected"] = text_out
            case_rows.append(entry)
        item: dict = {
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
        if suite.kind != IO:
            item.update(await _files_to_api(suite, teacher=teacher))
        out.append(item)
    return out


async def _files_to_api(suite: TaskTestSuite, *, teacher: bool) -> dict:
    """``SCRIPT``/``COMMAND`` 多出来的那几格，按看的人裁剪（见 ``suites_to_api``）。"""
    stored = [e for e in suite.test_files or [] if isinstance(e, dict)]
    out: dict = {
        "totalTimeLimitS": suite.total_time_limit_s,
        "studentLogLines": int(suite.student_log_lines or 0),
        "installDeps": bool(suite.install_deps),
    }
    if suite.kind == SCRIPT:
        out["testFileCount"] = len(stored)
    else:
        out["scoreSource"] = suite.score_source
        out["protectedPaths"] = list(suite.protected_paths or [])
    if not teacher:
        return out
    files = await test_files.read_files(stored)
    out["testFiles"] = [
        test_files.file_to_api(f) for f in files if f.role != test_files.PROTECTED
    ]
    if suite.kind == COMMAND:
        out["commands"] = list(suite.commands or [])
        out["protectedFiles"] = [
            test_files.file_to_api(f) for f in files if f.role == test_files.PROTECTED
        ]
    return out


def suite_settings(source: TaskTestSuite | SuiteLine) -> dict:
    """一套测试点的设置，评测主机那份任务要的样子（字段名同出题表单）。

    数据库里的一行和刚校验过的一条（试跑）都从这里出，两条评测路径送出去的设置
    逐字一致。文件和用例另外给（``judge.dispatch._prepare``）。
    """
    return {
        "kind": source.kind,
        "runtime": source.runtime,
        "codeDir": source.code_dir or "",
        "compileCmd": source.compile_cmd or "",
        "runCmd": source.run_cmd or "",
        "timeLimitMs": source.time_limit_ms,
        "memoryMb": source.memory_mb,
        "compare": source.compare,
        "tolerance": float(source.tolerance or 0.0),
        "commands": list(source.commands or []),
        "scoreSource": source.score_source,
        "totalTimeLimitS": source.total_time_limit_s,
        "studentLogLines": int(source.student_log_lines or 0),
    }
