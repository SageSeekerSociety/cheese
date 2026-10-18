#!/usr/bin/env python3
"""check-no-new-n-plus-one.py — the N+1 detector still tells a loop from a page.

WHY THIS EXISTS. 2026-10-18, #1825's runtime N+1 detector: `core/query_watch.py`
reports a query that repeats *from the same call site in the same shape*. That
judgement is the only thing standing between "a page is slow" and "we know
where"; a guard over the detector is what keeps it from quietly turning into
"count every statement twice" (which flags a page reading one row twice) or
into "flag every statement over a table" (which flags two unrelated queries).

WHAT IT DRIVES. It loads `backend/app/core/query_watch.py`, feeds each case a
sequence of (call site, statement) events, and compares ``RequestWatch.repeats``
with what the case says:

  * same fingerprint, same stack, 3 times -> reported;
  * same fingerprint, different stacks -> not reported;
  * same stack, different fingerprints -> not reported;
  * inside either whitelist -> not reported;
  * different literals (numbers, quoted strings, dollar-quoted strings, driver
    placeholders including ``%(name)s``, IN-list length) at one call site ->
    one fingerprint, so reported;
  * double-quoted *identifiers* are not literals: ``"topics"`` and
    ``"projects"`` stay different statements;
  * a statement the detector cannot fingerprint, and a report whose log sink
    raises, are swallowed: the detector never raises into a request.

It also drives one request through ``QueryWatchMiddleware``: the watch is alive
inside the request, both statements land in it, it is closed afterwards, and the
ContextVar is left as it was outside. Where SQLAlchemy and aiosqlite are
installed, it additionally drives a real async engine and requires the group's
stack to name this script's frame — the synthetic cases pass a stack in, so
without this one nothing would notice a greenlet hop that stopped naming the
caller. Without those packages the line says the engine path was not verified
rather than staying silent.

The module is imported by path and must stay importable without SQLAlchemy or
FastAPI (the CI helper lane has neither); its engine hook imports them when it
is actually installed, not when the file is read.

WHY NOT A SCAN OF THE TREE. A static "query inside a loop" matcher cannot tell
the N+1 from the page that reads one row twice, so it either freezes hundreds of
false positives or misses the real ones; the judgement this guard pins is the
runtime one. What it proves is that the detector still bites on the four cases
the report named — the mutation self-test beside it proves the cases would go
red if the judgement were loosened.

Usage: check-no-new-n-plus-one.py [--module PATH] [--json]
Exit 0 every case was judged as claimed, 1 a case was judged differently or
     the detector raised where it must not, 2 the detector could not be loaded.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODULE = REPO_ROOT / "backend" / "app" / "core" / "query_watch.py"

#: One call site, written the way ``query_watch.call_site`` writes it.
STACK = ("domain/topic/repositories/reads.py:120 list_for_project",)
#: A second, unrelated call site.
OTHER_STACK = ("domain/project/services.py:88 overview",)

SELECT_TOPICS = "SELECT * FROM topics WHERE id = {}"


@dataclass(frozen=True)
class Case:
    """A synthetic SQL sequence and the verdict the detector must give it."""

    name: str
    #: (call site, statement) in the order the request sent them.
    events: tuple[tuple[tuple[str, ...], str], ...]
    #: ``RequestWatch.repeats`` arguments beyond the defaults.
    options: dict = field(default_factory=dict)
    #: Whether the detector is expected to report this sequence.
    reported: bool = True


def _same_stack(statement: str, count: int, stack: tuple[str, ...] = STACK):
    return tuple((stack, statement) for _ in range(count))


CASES: tuple[Case, ...] = (
    Case(
        name="同指纹同栈 3 次 → 报",
        # One call site in a loop over three ids: the statements differ only in
        # the bound value, which is exactly the fingerprint's job.
        events=tuple(
            (STACK, SELECT_TOPICS.format(identifier)) for identifier in (1, 2, 3)
        ),
    ),
    Case(
        name="同指纹不同栈 → 不报",
        events=(
            (STACK, SELECT_TOPICS.format(1)),
            (OTHER_STACK, SELECT_TOPICS.format(2)),
        ),
        reported=False,
    ),
    Case(
        name="不同指纹（不同条件列）→ 不报",
        events=(
            (STACK, "SELECT * FROM topics WHERE status = 'open'"),
            (STACK, "SELECT * FROM topics WHERE owner_id = 7"),
        ),
        reported=False,
    ),
    Case(
        name="白名单按栈 → 不报",
        events=_same_stack(SELECT_TOPICS.format(1), 3),
        options={"ignore_stacks": ("domain/topic/repositories/reads.py",)},
        reported=False,
    ),
    Case(
        name="白名单按 SQL → 不报",
        events=_same_stack(SELECT_TOPICS.format(1), 3),
        options={"ignore_sql": ("from topics",)},
        reported=False,
    ),
    Case(
        name="数字不同的两条 → 同一指纹 → 报",
        events=(
            (STACK, "SELECT * FROM users WHERE id = 1 AND name = 'a'"),
            (STACK, "SELECT * FROM users WHERE id = 2 AND name = 'b'"),
        ),
    ),
    Case(
        name="IN 列表长度不同 → 同一指纹 → 报",
        events=(
            (STACK, "SELECT * FROM topics WHERE id IN (1, 2, 3)"),
            (STACK, "SELECT * FROM topics WHERE id IN (4, 5)"),
        ),
    ),
    Case(
        name="驱动占位符不同 → 同一指纹 → 报",
        events=(
            (STACK, "SELECT * FROM topics WHERE id = $1"),
            (STACK, "SELECT * FROM topics WHERE id = $2"),
        ),
    ),
    Case(
        name="dollar-quoted 字面量不同（$$）→ 同一指纹 → 报",
        events=(
            (STACK, "SELECT $$open$$ FROM topics WHERE id = 1"),
            (STACK, "SELECT $$closed$$ FROM topics WHERE id = 2"),
        ),
    ),
    Case(
        name="dollar-quoted 字面量不同（$tag$）→ 同一指纹 → 报",
        events=(
            (STACK, "SELECT $tag$open$tag$ FROM topics WHERE id = 1"),
            (STACK, "SELECT $tag$closed$tag$ FROM topics WHERE id = 2"),
        ),
    ),
    Case(
        name="命名占位符不同（%(name)s）→ 同一指纹 → 报",
        events=(
            (STACK, "SELECT * FROM topics WHERE id = %(topic_id)s"),
            (STACK, "SELECT * FROM topics WHERE id = %(other_id)s"),
        ),
    ),
    Case(
        name="双引号标识符不归一 → 不同表 → 不报",
        events=(
            (STACK, 'SELECT * FROM "topics" WHERE id = 1'),
            (STACK, 'SELECT * FROM "projects" WHERE id = 1'),
        ),
        reported=False,
    ),
    Case(
        name="只出现一次 → 不报",
        events=_same_stack(SELECT_TOPICS.format(1), 1),
        reported=False,
    ),
    Case(
        name="min_n_queries=3 时两次 → 不报",
        events=_same_stack(SELECT_TOPICS.format(1), 2),
        options={"min_n_queries": 3},
        reported=False,
    ),
)


class CannotJudge(Exception):
    """The detector could not be loaded, or a case could not be run."""


def load_module(path: Path) -> ModuleType:
    if not path.is_file():
        raise CannotJudge(f"{path} 不是文件")
    spec = importlib.util.spec_from_file_location("query_watch_under_test", path)
    if spec is None or spec.loader is None:
        raise CannotJudge(f"{path} 读不出模块")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve field annotations through ``sys.modules[cls.__module__]``
    # (this module defers annotations), so the module must be registered before
    # its class bodies run — ``module_from_spec`` alone does not register it.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # any import failure is "cannot judge"
        raise CannotJudge(f"{path} 导入失败: {exc}") from exc
    for name in ("RequestWatch", "QueryWatchMiddleware", "current_watch"):
        if not hasattr(module, name):
            raise CannotJudge(f"{path} 里没有 {name}")
    return module


def judge(module: ModuleType, case: Case) -> tuple[bool, str]:
    """Run one case; return (matched, explanation)."""
    watch = module.RequestWatch()
    for stack, statement in case.events:
        watch.note(statement, stack=stack)
    found = watch.repeats(**case.options)
    reported = bool(found)
    detail = "、".join(f"「{item.fingerprint}」×{item.count}" for item in found) or "无"
    if reported == case.reported:
        return True, f"报出 {detail}" if reported else "未报"
    wanted = "报" if case.reported else "不报"
    return False, f"应{wanted}，实际{detail}"


def _silence(module: ModuleType) -> None:
    logger = getattr(module, "logger", None)
    if logger is not None:
        # The boundary case really is an N+1; its log line would be noise here.
        logger.setLevel(100)


async def _boundary_check(module: ModuleType) -> None:
    inside: list = []

    async def inner(scope, receive, send) -> None:
        watch = module.current_watch()
        inside.append(watch)
        if watch is None:
            raise AssertionError("请求里没有 watch")
        watch.note(SELECT_TOPICS.format(1), stack=STACK)
        watch.note(SELECT_TOPICS.format(2), stack=STACK)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def send(message) -> None:
        return None

    middleware = module.QueryWatchMiddleware(inner)
    await middleware({"type": "http"}, None, send)

    watch = inside[0]
    if module.current_watch() is not None:
        raise AssertionError("请求结束后 ContextVar 没有恢复")
    if not watch.closed:
        raise AssertionError("请求结束后 watch 没有关闭")
    found = watch.repeats()
    if len(found) != 1 or found[0].count != 2:
        raise AssertionError(f"同一请求的两条语句没有并到一组: {found}")


def _never_raises_check(module: ModuleType) -> None:
    """The module's promise: a recording or a report never raises.

    A detector that turns a request into a 500 is worse than the query it
    failed to count, so a statement it cannot fingerprint must be dropped
    quietly, and a report that hits an unusable logger must not escape either.
    """
    class Sink:
        """Records both kinds of line, and fails only when asked to."""

        def __init__(self, *, explode: bool) -> None:
            self.explode = explode
            self.debugged: list = []
            self.warned: list = []

        def debug(self, *args, **_kwargs) -> None:
            self.debugged.append(args)

        def warning(self, *args, **_kwargs) -> None:
            self.warned.append(args)
            if self.explode:
                raise RuntimeError("log sink is broken")

    watch = module.RequestWatch()
    original = module.logger
    quiet = Sink(explode=False)
    module.logger = quiet
    try:
        try:
            watch.note(object())  # not a string: swallowed, not raised
        except Exception as exc:  # noqa: BLE001 - raising here is the failure
            # Kept separate from the report check below: sharing one try made
            # a note() that raised end this case before the report ever ran.
            raise AssertionError(
                f"note() 记录不了这条语句时抛了出来: {exc}"
            ) from exc
    finally:
        module.logger = original
    if watch.groups:
        raise AssertionError("无法指纹的语句被记了下来")
    if not quiet.debugged:
        # The module says a swallowed failure leaves a trail for a human.
        raise AssertionError("note() 吞掉语句时没有留下 debug 日志")
    watch.note("SELECT 1", stack=STACK)
    watch.note("SELECT 1", stack=STACK)

    sink = Sink(explode=True)
    module.logger = sink
    try:
        module.report(watch)
    except Exception as exc:  # noqa: BLE001 - that is exactly what must not happen
        raise AssertionError(f"report() 在日志坏掉时抛了出来: {exc}") from exc
    finally:
        module.logger = original
    if not sink.warned:
        # Every other case runs with the logger silenced and asserts the
        # return value, so a report that stopped logging entirely would pass
        # all of them; this is the one place the log line itself is required.
        raise AssertionError("report() 没有为重复组打出日志")


def _begin_finish_check(module: ModuleType) -> None:
    """``begin`` / ``finish`` are the request boundary for non-HTTP callers."""
    token = module.begin()
    watch = module.current_watch()
    if watch is None:
        raise AssertionError("begin() 之后 current_watch() 为空")
    watch.note(SELECT_TOPICS.format(1), stack=STACK)
    watch.note(SELECT_TOPICS.format(2), stack=STACK)
    found = module.finish(token)
    if len(found) != 1 or found[0].count != 2:
        raise AssertionError(f"begin/finish 的两条语句没有并到一组: {found}")
    if not watch.closed:
        # A task that inherited the watch must stop counting into a request
        # nobody will report.
        raise AssertionError("finish() 之后 watch 没有关闭")
    before = sum(group.count for group in watch.groups.values())
    watch.note(SELECT_TOPICS.format(3), stack=STACK)
    if sum(group.count for group in watch.groups.values()) != before:
        raise AssertionError("finish() 之后还往已关闭的 watch 里记语句")
    if module.current_watch() is not None:
        raise AssertionError("finish() 之后 ContextVar 没有恢复")


def _engine_scenario(module: ModuleType) -> str:
    """Drive a real async engine when this machine has one, else skip.

    The synthetic cases pass the stack in, so they cannot see whether the
    greenlet hop in ``call_site`` still names the caller: if it ever stops
    doing that, every statement gets an empty stack, unrelated call sites
    merge into one group, and a stack whitelist matches nothing. This case
    issues three statements from one loop through a watched engine and
    requires the group's stack to contain this script's frame.
    """
    try:
        import aiosqlite  # noqa: F401 - the async driver the engine needs
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
    except ImportError:
        return "skipped"

    class FakeApp:
        def __init__(self) -> None:
            self.middleware: list = []

        def add_middleware(self, middleware) -> None:
            self.middleware.append(middleware)

    async def run() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        try:
            app = FakeApp()
            module.install(app, engine=engine)
            if app.middleware != [module.QueryWatchMiddleware]:
                raise AssertionError(f"install() 没有挂上中间件: {app.middleware}")
            if not getattr(engine.sync_engine, "_cheese_query_watch_installed", False):
                raise AssertionError("install() 没有挂上引擎监听")
            with module.request_scope() as watch:
                async with engine.connect() as connection:
                    for identifier in range(3):
                        await connection.execute(
                            text(
                                f"select {identifier} from sqlite_master "
                                "where name = 'x'"
                            )
                        )
        finally:
            await engine.dispose()
        found = watch.repeats()
        if len(found) != 1 or found[0].count != 3:
            raise AssertionError(f"真实引擎的 3 次循环没有并成一组: {found}")
        if not any("check-no-new-n-plus-one" in frame for frame in found[0].stack):
            raise AssertionError(f"栈里没有本文件的帧: {found[0].stack}")

    asyncio.run(run())
    return "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--module",
        type=Path,
        default=DEFAULT_MODULE,
        help="探测器模块，默认 backend/app/core/query_watch.py",
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出")
    args = parser.parse_args()

    try:
        module = load_module(args.module.resolve())
        _silence(module)
        failures: list[dict[str, str]] = []
        #: Counted as they are judged, so the number reported is what ran —
        #: a case deleted from CASES lowers it instead of leaving a constant
        #: that still claims the old total.
        ran = 0
        for case in CASES:
            ran += 1
            try:
                matched, detail = judge(module, case)
            except Exception as exc:
                # A detector that raises on a legal statement is a regression
                # of the detector, not a tree we cannot judge: record it and
                # keep running the rest, so one crash does not hide every
                # other case behind exit 2.
                failures.append(
                    {"case": case.name, "detail": f"探测器抛了异常: {exc}"}
                )
                continue
            if not matched:
                failures.append({"case": case.name, "detail": detail})
        try:
            asyncio.run(_boundary_check(module))
            boundary = "ok"
        except CannotJudge:
            raise
        except Exception as exc:
            boundary = f"失败: {exc}"
            failures.append({"case": "请求边界（中间件 + ContextVar）", "detail": boundary})
        ran += 1
        try:
            _never_raises_check(module)
        except Exception as exc:
            failures.append({"case": "记录与报告不抛异常", "detail": str(exc)})
        ran += 1
        try:
            _begin_finish_check(module)
        except Exception as exc:
            failures.append({"case": "begin/finish 边界", "detail": str(exc)})
        ran += 1
        try:
            engine = _engine_scenario(module)
        except Exception as exc:
            engine = f"失败: {exc}"
            failures.append({"case": "真实引擎（greenlet 栈）", "detail": engine})
        ran += 1
    except CannotJudge as exc:
        print(f"N+1 探测器守卫判不了: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(
            json.dumps(
                {
                    "module": str(args.module),
                    "cases": ran,
                    "engine": engine,
                    "failures": failures,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif failures:
        for failure in failures:
            print(
                f"场景「{failure['case']}」判错了：{failure['detail']}",
                file=sys.stderr,
            )
        print(
            "N+1 探测器的判据变了：同调用栈 + 同 SQL 指纹两条同时成立才算 N+1，"
            "指纹把数字、引号字符串、IN 列表归一成 ?",
            file=sys.stderr,
        )
    else:
        engine_note = (
            "真实引擎路径已验证"
            if engine == "ok"
            else "真实引擎路径未验证（本机没有 SQLAlchemy/aiosqlite）"
        )
        print(
            f"N+1 探测器守卫通过（{ran} 个场景：判定 4 类、指纹归一、"
            f"两维白名单、请求边界、begin/finish、记录/报告不抛异常；{engine_note}）"
        )

    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
