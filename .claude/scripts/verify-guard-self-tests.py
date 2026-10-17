#!/usr/bin/env python3
"""verify-guard-self-tests.py — every local guard's self-test must actually bite.

WHY THIS EXISTS: a guard declared in `.pre-commit-config.yaml` is trusted to
judge the tree only because its `--self-test` proves the guard still fires on a
deliberate violation (the "Guards self-test" step of
`.github/workflows/repo-guards.yml` says exactly that). That proof is itself
never checked. A `--self-test` that always prints PASS looks identical to one
that works whenever the tree is clean — the failure mode the guards exist to
prevent. Two rules that only memory enforced are what this replaces: a table of
counts whose self-test could not go red, and a seam test that collected nothing
while its assertions still passed.

WHAT IT CHECKS, per guard named by a `repo: local` hook:

  have a self-test   the guard answers `--self-test`, or a `test_<stem>.py` /
                     `test-<stem>.py` sits beside it. Neither is a finding.
  run green          that self-test exits 0 and prints a success line (PASS, or
                     unittest's OK).
  bite               a copy of the guard with its failure signals silenced makes
                     the self-test exit non-zero — go red — instead of staying
                     green. Green is what CI reads, so a self-test that stays
                     green on a silenced guard guards nothing, and is the finding
                     this tool exists to raise.

HOW THE SILENCING WORKS. It runs on a throwaway copy in the guard's own
directory — same directory so the guard still resolves its siblings and its repo
root through `__file__`, a distinct dot-prefixed name so no existing file is
touched — and the copy is removed in a `finally`. A Python guard is silenced by
AST-guided edits to the statements *inside functions* other than `self_test()`
and the `__main__` block, and only to the statements that *emit* a failure: a
`return` of a non-zero code becomes `return 0` — an int, a tuple whose first
element is the code (`return 1, lines, record`), or a conditional
(`return 0 if verdict.ok else 1`) — and `sys.exit(<non-zero>)` /
`raise SystemExit(<non-zero>)` become their zero form. Collectors
(`findings.append(…)`, `lines.extend(…)`) are deliberately left alone: nulling
one also breaks the guard's own scan, so the self-test goes red because a *clean*
fixture of its own broke, not because the failure signal was taken away — a red
that proves nothing, and the tool would bless a self-test that never looks at a
violation. The entry point is left alone on purpose: silencing it would stop the
self-test from running at all, which reads as red without the self-test having
noticed anything. A shell guard's non-zero `exit`/`return` lines are rewritten
to `exit 0`/`return 0` instead — the same rule applied without an AST — and its
whole `if … SELF_TEST …; then` block, `self_fail` included, is left untouched.
A verdict written as a variable (`exit "$CODE"`) offers no literal to rewrite
and is reported as UNVERIFIED rather than guessed at.

A copy that goes green on a *silenced* guard is reported as a finding — unless
the guard hands the work to another script file: a `.py` name that is not its
own in the command position of a spawn (the program a `subprocess`/`os.exec*`
call runs, `-m`/`-c` included), or a spawn under a self-test condition. There
the mutation never reaches the process that judges. Arguments *after* the
program are the guard's own command line — a `.py` fixture passed as input does
not make the guard foreign — and a guard that re-runs *itself*
(`Path(__file__)`) is judged normally: the copy is what runs, so the mutation
travels with it.

WHAT CANNOT BE JUDGED, and is reported as UNVERIFIED rather than as a pass: a
guard whose self-test lives in a sibling `test_<stem>.py`, or whose `--self-test`
spawns one (silencing a copy of the guard cannot reach the process the test
runs, and editing the tree under the test is not this tool's business); a copy
that will not compile or could not be built; and a copy whose self-test prints
nothing, or stops printing the success line while still exiting 0 — there the
run went quiet instead of red, so neither verdict is earned. Give such a guard an
inline `--self-test` that judges the guard in its own process to be checked.

    python3 .claude/scripts/verify-guard-self-tests.py
    python3 .claude/scripts/verify-guard-self-tests.py --guard check-file-sizes.py
    python3 .claude/scripts/verify-guard-self-tests.py --json
    python3 .claude/scripts/verify-guard-self-tests.py --self-test

Exit code: 0 every guard in scope is covered and biting; 1 at least one finding;
2 nothing to report but something could not be judged.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

GUARD_DIR = Path(".claude/scripts")
CONFIG_NAME = ".pre-commit-config.yaml"
DEFAULT_TIMEOUT = 300.0
#: The lines a green self-test ends on: its own PASS line, or unittest's OK.
SUCCESS_RE = re.compile(r"^(?:PASS|OK)\b")

#: Statuses that are a finding (a guard that must be fixed).
FINDING_STATUSES = frozenset({"missing-self-test", "self-test-not-green", "self-test-does-not-bite"})

#: One repair instruction per finding, so a red run says what to do next.
HINTS = {
    "missing-self-test": (
        "给它加一个自测：喂进一个故意违规的夹具，断言它报错（非 0 退出和一句修法），"
        "并在通过时打印 PASS 行；或者把一个 test_<脚本>.py 放在它旁边。"
    ),
    "self-test-not-green": (
        "自测在当前树上没有打印 PASS 或退出了非 0；先把它修绿，再谈咬合力。"
    ),
    "self-test-does-not-bite": (
        "把守卫的失败信号（非 0 的 return、exit、sys.exit、SystemExit）静音之后，自测仍然绿着——"
        "它没有在看守卫报不报违规。让它喂进一个故意违规的夹具，断言守卫真的以非 0 退出码报错并印出修法。"
    ),
    "unverified": (
        "这次判不了（原因见 detail），不要当成通过。"
    ),
}

#: The `subprocess` entry points that start another program.
_SUBPROCESS_CALLS = frozenset({"run", "call", "check_call", "check_output", "Popen"})

#: Names that run a Python script: `python`, `python3`, `python3.12`.
_PYTHON_RE = re.compile(r"^python(\d+(\.\d+)?)?$")

#: The line that opens a shell guard's self-test block; everything from here on
#: belongs to the self-test itself and is left as it is.
_SHELL_SELF_TEST_RE = re.compile(r"^\s*if\b.*\bSELF_TEST\b.*;\s*then\b")

#: A shell guard's verdict: a non-zero `exit` or `return`. Silencing the verdict
#: leaves the scan — and anything it prints or writes — exactly where it was.
_SHELL_NONZERO_RE = re.compile(r"(?<![\w$])(exit|return)\s+([1-9]\d*)\b")


@dataclass
class Guard:
    hook: str
    path: Path
    kind: str  # "inline" | "sibling" | "none"
    command: list[str] = field(default_factory=list)


@dataclass
class Outcome:
    status: str
    detail: str = ""


def local_guard_scripts(config_text: str, root: Path) -> list[tuple[str, Path]]:
    """Every script a `repo: local` hook's `entry:` names, resolved to a file.

    The config is read as text, not through a YAML parser: none is guaranteed on
    a bare `python3`, and only `id:` and `entry:` are needed. A path that does not
    exist relative to the repo root is not one of ours (a hook that `cd`s first
    names a path under a subtree), so resolution is the filter.
    """
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    hook = "?"
    for line in config_text.splitlines():
        id_match = re.match(r"\s*-\s*id:\s*(\S+)\s*$", line)
        if id_match:
            hook = id_match.group(1)
            continue
        entry_match = re.match(r"\s*entry:\s*(.+?)\s*$", line)
        if not entry_match:
            continue
        for token in re.findall(r"[\w./-]+\.(?:py|sh)", entry_match.group(1)):
            candidate = root / token
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in seen or not resolved.is_file():
                continue
            seen.add(resolved)
            found.append((hook, resolved))
    return found


def self_test_kind(path: Path) -> tuple[str, list[str]]:
    """How the guard carries its self-test, and the command that runs it."""
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "none", []
    if "--self-test" in source:
        interpreter = sys.executable if path.suffix == ".py" else "bash"
        return "inline", [interpreter, str(path), "--self-test"]
    stem = path.stem
    for name in (f"test_{stem.replace('-', '_')}.py", f"test-{stem}.py"):
        sibling = path.with_name(name)
        if sibling.is_file():
            return "sibling", [sys.executable, str(sibling)]
    return "none", []


def _self_test_lines(tree: ast.Module) -> set[int]:
    """Line numbers of every `self_test` function, so they are left alone.

    Silencing the self-test's own failure reporting is what makes a no-op read
    as passable; the point is to break only the guard the self-test judges.
    """
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "self_test":
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                lines.add(line)
    return lines


def _main_block_lines(tree: ast.Module, source: str) -> set[int]:
    """Line numbers of the `if __name__ == "__main__":` block, left alone.

    Silencing the entry point would stop the self-test from running at all, and
    a self-test that never ran also never prints its success line — which would
    read as a bite without the self-test having noticed anything.
    """
    lines: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and "__main__" in (ast.get_source_segment(source, node.test) or ""):
            for line in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                lines.add(line)
    return lines


def _spawn_target(node: ast.AST) -> bool:
    """Is this expression a `subprocess.*` / `os.exec*` call that starts a program?"""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    target = node.func.value
    if not isinstance(target, ast.Name):
        return False
    return (target.id == "subprocess" and node.func.attr in _SUBPROCESS_CALLS) or (
        target.id == "os" and node.func.attr.startswith("exec")
    )


def delegated_self_test(tree: ast.Module, source: str) -> bool:
    """Does the `--self-test` path hand off to another program, under a test flag?

    A guard that answers `--self-test` by running a sibling test file judges
    itself in a *different* process, reading the real file rather than the copy
    this tool silences; nothing a copy does can reach it.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test_source = ast.get_source_segment(source, node.test) or ""
        if "self_test" not in test_source and "self-test" not in test_source:
            continue
        if any(_spawn_target(inner) for inner in ast.walk(node)):
            return True
    return False


def _spawned_script(node: ast.Call) -> ast.AST | None:
    """The expression naming the file a spawn call executes, if it runs one.

    Only the program in the command position counts. Everything after it is the
    guard's own command line — a `--root <dir>` and any fixture name ending in
    `.py` are arguments, not scripts — and a non-Python program such as
    `git ls-files '*.py'` runs no script at all. For `python -m` / `python -c`
    the constant itself comes back, since what runs is a module or inline code.
    """
    if not isinstance(node, ast.Call) or not node.args:
        return None
    command = node.args[0]
    if not isinstance(command, (ast.List, ast.Tuple)):
        return command
    parts = list(command.elts)
    if not parts:
        return None
    program, rest = parts[0], parts[1:]
    if not (isinstance(program, ast.Constant) and isinstance(program.value, str)):
        # `sys.executable`, or a path assembled at run time: the script follows.
        return _skip_interpreter_flags(rest)
    if _PYTHON_RE.match(Path(program.value).name):
        return _skip_interpreter_flags(rest)
    return program if program.value.endswith(".py") else None


def _skip_interpreter_flags(rest: list[ast.expr]) -> ast.AST | None:
    """The first element that is not an interpreter flag.

    `-m` and `-c` are handed back as themselves: they run a module or inline
    code rather than this file, which is what `_runs_foreign_script` reports.
    """
    for part in rest:
        if not (isinstance(part, ast.Constant) and isinstance(part.value, str)):
            return part
        if part.value in ("-m", "-c"):
            return part
        if part.value.startswith("-"):
            continue
        return part
    return None


def _runs_foreign_script(script: ast.AST | None, own_name: str) -> bool:
    """Does this expression name a `.py` file other than the guard itself?"""
    if script is None:
        return False
    if isinstance(script, ast.Constant) and isinstance(script.value, str):
        if script.value in ("-m", "-c"):
            return True
        return script.value.endswith(".py") and Path(script.value).name != own_name
    # `str(Path(__file__).with_name("test_x.py"))` names another file inside the
    # expression; `str(Path(__file__).resolve())` names this one and holds no
    # literal at all. Only the file *name* is compared, as the path around it
    # makes no difference to which file Python loads.
    for inner in ast.walk(script):
        if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
            if inner.value.endswith(".py") and Path(inner.value).name != own_name:
                return True
    return False


def spawns_foreign_file(tree: ast.Module, own_name: str) -> bool:
    """Does the guard run another script file, rather than re-running itself?

    A guard that spawns `Path(__file__)` re-runs the copy this tool silences, so
    the mutation travels into the child and the self-test still sees it. One that
    names a different `.py` in the command position — a sibling test file,
    `python -m unittest` — runs the real script in a fresh process where the
    mutation does not exist, so its green says nothing of this copy.
    """
    return any(
        _runs_foreign_script(_spawned_script(node), own_name)
        for node in ast.walk(tree)
        if _spawn_target(node)
    )


def _exit_code_names(tree: ast.Module) -> set[str]:
    """Names assigned a non-zero integer at module level: `EXIT_CANNOT_JUDGE = 2`.

    Guards name their exit codes as often as they spell them, and `return
    EXIT_CANNOT_JUDGE` is the same failure signal as `return 2`.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and _nonzero_code(node.value):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


def _nonzero_code(node: ast.AST, names: frozenset[str] = frozenset()) -> bool:
    """Is this a non-zero integer code — spelled out, or one of the module's names?"""
    if isinstance(node, ast.Name) and node.id in names:
        return True
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
        and node.value != 0
    )


def _is_zero(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == 0


def _code_span(value: ast.AST, names: frozenset[str]) -> tuple[int, int] | None:
    """The span to overwrite with `0` when a `return` carries a failure code.

    The whole value for a bare `return 1`, and only the code itself for shapes
    that answer something else as well — a tuple like `return 1, lines, record`
    (many guards return the code beside their findings) or a conditional like
    `return 0 if verdict.ok else 1` — so the copy stays a working guard that
    simply never reports a failure.
    """
    if _nonzero_code(value, names):
        return (value.col_offset, value.end_col_offset)
    if isinstance(value, ast.Tuple) and value.elts and _nonzero_code(value.elts[0], names):
        head = value.elts[0]
        return (head.col_offset, head.end_col_offset)
    if isinstance(value, ast.IfExp):
        branches = (value.body, value.orelse)
        if all(_nonzero_code(branch, names) or _is_zero(branch) for branch in branches):
            return (value.col_offset, value.end_col_offset)
    return None


def _exit_arg(call: ast.AST) -> tuple[ast.expr, tuple[int, int]] | None:
    """The argument of a `sys.exit(x)` / `SystemExit(x)` that is not already 0."""
    if not isinstance(call, ast.Call) or not call.args:
        return None
    func = call.func
    is_sys_exit = (
        isinstance(func, ast.Attribute)
        and func.attr == "exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
    )
    is_system_exit = isinstance(func, ast.Name) and func.id == "SystemExit"
    if not (is_sys_exit or is_system_exit):
        return None
    arg = call.args[0]
    # `SystemExit(*codes)` keeps the star: there is no expression to wrap.
    if isinstance(arg, ast.Starred) or _is_zero(arg):
        return None
    return arg, (arg.col_offset, arg.end_col_offset)


def _python_edits(
    tree: ast.Module, source: str, skip: set[int]
) -> dict[int, list[tuple[int, int, bytes | None]]]:
    """Byte-slice replacements that turn the guard's failure signals into nothing.

    Only single-line nodes inside a function are rewritten — module level set-up
    is left working, so a copy that dies did so while running, not on import —
    and an outer node chosen for replacement stops the walk so a nested call is
    not also rewritten. What gets rewritten is only where the guard *emits* its
    verdict: `return`s of a non-zero code and non-zero `sys.exit`/`SystemExit`.
    An exit argument that is a call — `raise SystemExit(main(argv))` — keeps
    running and only its result is discarded: replacing it with `0` would delete
    whatever that call printed or wrote, and a red caused by that proves nothing.
    """
    edits: dict[int, list[tuple[int, int, bytes]]] = {}
    skipped = skip | _main_block_lines(tree, source)
    codes = frozenset(_exit_code_names(tree))

    def record(node: ast.AST, span: tuple[int, int], replacement: bytes | None = b"0") -> None:
        # `None` means "wrap the original in place": it still runs, its result is
        # thrown away. Rewriting a call to `0` would take its side effects with it.
        edits.setdefault(node.lineno, []).append((span[0], span[1], replacement))

    def visit(node: ast.AST, in_function: bool) -> None:
        single = (
            getattr(node, "lineno", None)
            and getattr(node, "end_lineno", None)
            and node.lineno == node.end_lineno
            and node.lineno not in skipped
        )
        if single and in_function:
            if isinstance(node, ast.Call):
                found = _exit_arg(node)
                if found is not None:
                    arg, span = found
                    record(node, span, b"0" if isinstance(arg, ast.Constant) else None)
                    return
            if isinstance(node, ast.Raise):
                found = _exit_arg(node.exc) if node.exc is not None else None
                if found is not None:
                    arg, span = found
                    record(node, span, b"0" if isinstance(arg, ast.Constant) else None)
                    return
            if isinstance(node, ast.Return) and node.value is not None:
                span = _code_span(node.value, codes)
                if span is not None:
                    record(node, span)
                    return
        enters = isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        for child in ast.iter_child_nodes(node):
            visit(child, in_function or enters)

    visit(tree, False)
    return edits


def mutate_python(source: str) -> tuple[str | None, str]:
    """A copy of a Python guard whose failure signals are silenced."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return None, f"守卫无法解析：{exc}"
    edits = _python_edits(tree, source, _self_test_lines(tree))
    if not edits:
        return None, "没有找到可以静音的失败信号（函数里没有非 0 的 return、sys.exit 或 SystemExit）"
    raw = source.encode("utf-8").splitlines(keepends=True)
    count = 0
    for lineno in sorted(edits):
        line = raw[lineno - 1]
        limit = len(line)
        for start, end, replacement in sorted(edits[lineno], key=lambda item: -item[0]):
            if end > limit or start > end:
                continue
            if replacement is None:
                replacement = b"(" + line[start:end] + b", 0)[1]"
            line = line[:start] + replacement + line[end:]
            limit = start
            count += 1
        raw[lineno - 1] = line
    if count == 0:
        return None, "没有找到可以静音的失败信号"
    mutant = b"".join(raw).decode("utf-8")
    try:
        compile(mutant, "<silenced-guard>", "exec")
    except SyntaxError as exc:
        return None, f"静音改写后无法编译：{exc}"
    return mutant, ""


def mutate_shell(source: str) -> tuple[str | None, str]:
    """A copy of a shell guard that reaches every conclusion and returns clean.

    Only the failure verdict is rewritten: a non-zero `exit` or `return` becomes
    `exit 0` / `return 0`. The scan runs to the end, so everything it prints,
    records or accumulates still happens — a self-test that only looks at those
    keeps passing, which is exactly what has to be reported as toothless. The
    self-test block itself, and its `self_fail` helper, are left untouched.
    """
    lines = source.splitlines(keepends=True)
    block = _shell_self_test_span(lines)
    if block is None:
        return None, "找不到 `if … SELF_TEST …; then` 自测块，静音不了守卫的结论"
    start, end = block
    out: list[str] = []
    count = 0
    for index, line in enumerate(lines):
        if not start <= index <= end and not line.lstrip().startswith("#"):
            line, replaced = _SHELL_NONZERO_RE.subn(r"\1 0", line)
            count += replaced
        out.append(line)
    if count == 0:
        return None, "自测块之外没有非 0 的 exit/return，静音不了守卫的结论"
    return "".join(out), ""


def _shell_self_test_span(lines: list[str]) -> tuple[int, int] | None:
    """The line range of the `if … SELF_TEST …; then` block, both ends inclusive.

    The block owns its own failure reporting (`self_fail`, inline `exit 1`), so
    none of it may be rewritten. It ends at the `fi` written at the same
    indentation as its `if`; a missing `fi` runs to the end of the file, where
    declining to silence anything is still honest.
    """
    for start, line in enumerate(lines):
        if not _SHELL_SELF_TEST_RE.match(line):
            continue
        indent = len(line) - len(line.lstrip())
        for end in range(start + 1, len(lines)):
            if lines[end].strip() == "fi" and len(lines[end]) - len(lines[end].lstrip()) == indent:
                return start, end
        return start, len(lines) - 1
    return None


def _prints_success(text: str) -> bool:
    return any(SUCCESS_RE.match(line.strip()) for line in text.splitlines())


def _tail(*texts: str) -> str:
    for text in texts:
        lines = [line for line in text.splitlines() if line.strip()]
        if lines:
            return lines[-1].strip()[:200]
    return ""


def _run(command: list[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)


def check_guard(guard: Guard, root: Path, timeout: float) -> Outcome:
    if guard.kind == "none":
        return Outcome("missing-self-test", "既没有 --self-test，旁边也没有 test_<脚本>.py")
    if guard.kind == "sibling":
        try:
            pristine = _run(guard.command, root, timeout)
        except subprocess.TimeoutExpired:
            return Outcome("unverified", f"旁边的测试超过 {timeout:g}s 没结束")
        except OSError as exc:
            return Outcome("unverified", f"旁边的测试起不来：{exc}")
        if pristine.returncode != 0 or not _prints_success(pristine.stdout + pristine.stderr):
            return Outcome("self-test-not-green", _tail(pristine.stdout, pristine.stderr))
        return Outcome(
            "unverified",
            "自测在旁边的测试文件里；静音守卫需要改工作树，本核验器不做。"
            "给守卫加一个内联 --self-test 才能被核验咬合力",
        )

    try:
        pristine = _run(guard.command, root, timeout)
    except subprocess.TimeoutExpired:
        return Outcome("unverified", f"自测超过 {timeout:g}s 没结束")
    except OSError as exc:
        return Outcome("unverified", f"自测起不来：{exc}")
    if pristine.returncode != 0 or not _prints_success(pristine.stdout + pristine.stderr):
        return Outcome("self-test-not-green", _tail(pristine.stdout, pristine.stderr))

    try:
        source = guard.path.read_text(encoding="utf-8")
    except OSError as exc:
        return Outcome("unverified", f"守卫读不出来：{exc}")
    if guard.path.suffix == ".py":
        try:
            tree = ast.parse(source)
        except SyntaxError as exc:
            return Outcome("unverified", f"守卫无法解析：{exc}")
        if delegated_self_test(tree, source) or spawns_foreign_file(tree, guard.path.name):
            return Outcome(
                "unverified",
                "自测在旁边的测试文件里（--self-test 起的是另一个进程，读的是真文件）；"
                "静音守卫的副本到不了它。给守卫加一个在自身进程里判自己的内联 --self-test 才能核验",
            )
        mutant, reason = mutate_python(source)
    elif guard.path.suffix == ".sh":
        mutant, reason = mutate_shell(source)
    else:
        mutant, reason = None, f"不认识的守卫类型 {guard.path.suffix}"
    if mutant is None:
        return Outcome("unverified", reason)

    mutant_path = guard.path.with_name(f".{guard.path.name}.silenced-{uuid.uuid4().hex[:8]}")
    try:
        mutant_path.write_text(mutant, encoding="utf-8")
        command = [mutant_path.as_posix() if part == guard.path.as_posix() else part for part in guard.command]
        try:
            silenced = _run(command, root, timeout)
        except subprocess.TimeoutExpired:
            return Outcome("unverified", f"静音后的自测超过 {timeout:g}s 没结束")
        except OSError as exc:
            return Outcome("unverified", f"静音后的自测起不来：{exc}")
    finally:
        mutant_path.unlink(missing_ok=True)

    output = silenced.stdout + silenced.stderr
    if not output.strip():
        return Outcome("unverified", f"静音后的自测没有任何输出（退出码 {silenced.returncode}）：既没红也没绿")
    if silenced.returncode == 0:
        if _prints_success(output):
            return Outcome("self-test-does-not-bite", f"守卫被静音后自测仍然绿着：{_tail(silenced.stdout, silenced.stderr)}")
        return Outcome(
            "unverified",
            f"静音后自测不再打印成功行，却仍然退出 0：{_tail(silenced.stdout, silenced.stderr)}",
        )
    if "Traceback (most recent call last)" in output:
        return Outcome("ok", f"静音后自测崩了（退出码 {silenced.returncode}）：{_tail(silenced.stderr, silenced.stdout)}")
    return Outcome("ok", f"静音后自测变红（退出码 {silenced.returncode}）：{_tail(silenced.stderr, silenced.stdout)}")


def evaluate(
    config_path: Path, root: Path, timeout: float, only: set[str] | None = None
) -> list[tuple[str, Path, Outcome, bool]]:
    """Judge every local guard named by the config. Returns (hook, path, outcome, in_scope)."""
    text = Path(config_path).read_text(encoding="utf-8")
    guard_dir = (Path(root) / GUARD_DIR).resolve()
    results: list[tuple[str, Path, Outcome, bool]] = []
    for hook, path in local_guard_scripts(text, Path(root)):
        if only and path.name not in only:
            continue
        if path.parent != guard_dir:
            results.append((hook, path, Outcome("skipped", "不在 .claude/scripts 下，本核验器不管"), False))
            continue
        kind, command = self_test_kind(path)
        outcome = check_guard(Guard(hook, path, kind, command), Path(root), timeout)
        results.append((hook, path, outcome, True))
    return results


def report(results: list[tuple[str, Path, Outcome, bool]]) -> tuple[int, int]:
    checked = [item for item in results if item[3]]
    skipped = [item for item in results if not item[3]]
    if not checked:
        print("没有在 .pre-commit-config.yaml 里找到 .claude/scripts 下的本地守卫", file=sys.stderr)
        return 0, 1
    width = max(len(path.name) for _, path, _, _ in checked)
    findings = 0
    unverified = 0
    for hook, path, outcome, _ in checked:
        label = outcome.status.upper()
        print(f"{label:<22} {path.name:<{width}}  {outcome.detail}")
        if outcome.status in FINDING_STATUSES:
            findings += 1
            print(f"  {'':<20} 修法：{HINTS[outcome.status]}")
        elif outcome.status == "unverified":
            unverified += 1
    if skipped:
        print(f"（另有 {len(skipped)} 个本地钩子的脚本不在 .claude/scripts 下，未核验：{', '.join(path.name for _, path, _, _ in skipped)}）")
    print(f"共 {len(checked)} 个守卫：有发现 {findings} 个，判不了 {unverified} 个")
    return findings, unverified


def self_test() -> int:
    """Prove the engine tells a biting self-test from a no-op one, in a throwaway repo."""
    with tempfile.TemporaryDirectory(prefix="verify-guard-self-tests-") as raw:
        root = Path(raw)
        config = write_synthetic_repo(root)
        results = {path.name: outcome for _, path, outcome, _ in evaluate(config, root, 60.0)}
    expected = {
        "biting-guard.py": "ok",
        "tuple-return-guard.py": "ok",
        "named-exit-guard.py": "ok",
        "plain-return-guard.py": "ok",
        "named-return-guard.py": "ok",
        "fixture-arg-guard.py": "ok",
        "shell-biting-guard.sh": "ok",
        "fake-green-guard.py": "self-test-does-not-bite",
        "shell-fake-green-guard.sh": "self-test-does-not-bite",
        "clean-only-self-test-guard.py": "self-test-does-not-bite",
        "shell-clean-only-self-test-guard.sh": "self-test-does-not-bite",
        "call-exit-guard.py": "self-test-does-not-bite",
        "no-self-test-guard.py": "missing-self-test",
        "not-green-guard.py": "self-test-not-green",
        "delegating-guard.py": "unverified",
        "sibling-guard.py": "unverified",
    }
    failures = []
    for name, want in expected.items():
        got = results.get(name)
        if got is None:
            failures.append(f"{name}: 没有被核验到")
        elif got.status != want:
            failures.append(f"{name}: 判成 {got.status}（{got.detail}），应为 {want}")
    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "PASS: verify-guard-self-tests self-test（失败信号的每种写法都判得出咬合力——条件返回、"
        "元组返回、裸 return、模块级命名常量、SystemExit、SystemExit(调用)、shell 的 exit/return；"
        "参数里的 .py 不算委派；只查干净夹具的假绿被检出（Python 与 shell 各一）、空转假绿被检出、"
        "缺自测与没变绿各自报出、委派给旁置测试文件的判不了）"
    )
    return 0


#: Guards for the self-test: one of each verdict, and one of each shape the
#: failure signal takes (a conditional return, a tuple return, a named code).
SYNTHETIC_GUARDS = {
    "biting-guard.py": '''#!/usr/bin/env python3
"""A guard whose --self-test proves it fires on a deliberate violation."""
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 0 if not problems else 1


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1 or "修法" not in done.stderr:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad 并给出修法，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（.bad 文件被报出并给了修法）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "tuple-return-guard.py": '''#!/usr/bin/env python3
"""A guard whose failure comes back as the first element of a tuple."""
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    if problems:
        return 1, problems
    return 0, []


def main(argv):
    if "--self-test" in argv:
        return self_test()
    code, _ = report(Path(argv[1]) if len(argv) > 1 else Path("."))
    return code


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成 tuple 守卫自测（.bad 文件被报出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "named-exit-guard.py": '''#!/usr/bin/env python3
"""A guard that reports through a named constant and a SystemExit raise."""
import sys
from pathlib import Path

EXIT_FOUND = 1


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    if problems:
        raise SystemExit(EXIT_FOUND)
    sys.exit(0)


def main(argv):
    if "--self-test" in argv:
        return self_test()
    report(Path(argv[1]) if len(argv) > 1 else Path("."))
    return 0


def self_test():
    import subprocess
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成命名退出码守卫自测（.bad 文件被报出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "plain-return-guard.py": '''#!/usr/bin/env python3
"""A guard whose only failure signal is a bare `return 1`."""
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    if problems:
        return 1
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return report(Path(argv[1]) if len(argv) > 1 else Path("."))


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成裸 return 守卫自测（.bad 文件被报出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "named-return-guard.py": '''#!/usr/bin/env python3
"""A guard that returns a named constant instead of raising it."""
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_FOUND = 1


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def report(root):
    problems = violations_in(root)
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    if problems:
        return EXIT_FOUND
    return 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return report(Path(argv[1]) if len(argv) > 1 else Path("."))


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)], capture_output=True, text=True
        )
        if done.returncode != 1:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成命名 return 守卫自测（.bad 文件被报出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "fixture-arg-guard.py": '''#!/usr/bin/env python3
"""A guard that passes a `.py` fixture name to itself as an argument."""
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 0 if not problems else 1


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root), "fixture.py"],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成带 .py 参数的守卫自测（.bad 文件被报出）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "shell-biting-guard.sh": '''#!/usr/bin/env bash
# A shell guard whose --self-test proves it fires on a deliberate violation.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  local root="$1" bad
  bad="$(find "$root" -name '*.bad' -print 2>/dev/null || true)"
  [ -z "$bad" ] && return 0
  echo "$bad: 修法：删掉这些文件" >&2
  return 1
}

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp" && self_fail "一个 .bad 文件必须报错"
  echo "PASS: 合成 shell 守卫自测（.bad 文件被报出）"
  exit 0
fi

scan "${1:-.}"
''',
    "fake-green-guard.py": '''#!/usr/bin/env python3
"""A guard whose --self-test prints PASS and checks nothing."""
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        print("PASS: 合成假绿守卫自测")
        return 0
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "shell-fake-green-guard.sh": '''#!/usr/bin/env bash
# A shell guard whose --self-test prints PASS and checks nothing.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  local root="$1" bad
  bad="$(find "$root" -name '*.bad' -print 2>/dev/null || true)"
  [ -z "$bad" ] && return 0
  echo "$bad: 修法：删掉这些文件" >&2
  return 1
}

if [ "$SELF_TEST" = 1 ]; then
  echo "PASS: 合成假绿 shell 守卫自测"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-clean-only-self-test-guard.sh": '''#!/usr/bin/env bash
# A shell guard whose --self-test only ever asserts the clean path and the
# --json record. Its red under a whole-function mutation came from the record
# disappearing, not from the verdict being taken away.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

FAILED=0

counts() {
  find "$1" -name '*.bad' -print 2>/dev/null | grep -c . || true
}

record() {
  printf '{"files": %s}\\n' "$(counts "$1")"
}

scan() {
  local n
  n="$(counts "$1")"
  if [ "$n" -gt 0 ]; then
    echo "$n 个 .bad 文件：修法：删掉它们" >&2
    FAILED=1
  fi
}

if [ "$SELF_TEST" = 1 ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp"
  [ "$FAILED" = 0 ] || self_fail "合成：干净树必须通过"
  out="$(record "$tmp")"
  case "$out" in
    *'"files": 0'*) ;;
    *) self_fail "合成：记录里要有文件数（得到 $out）" ;;
  esac
  echo "PASS: 合成 shell 守卫自测（干净树通过，记录成立）"
  exit 0
fi

scan "${1:-.}"
record "${1:-.}"
[ "$FAILED" = 1 ] && exit 1
exit 0
''',
    "clean-only-self-test-guard.py": '''#!/usr/bin/env python3
"""A guard whose --self-test only ever checks that a clean scan comes back.

This is the shape a broad "null the collector" mutation used to bless. Nulling
`found.append(...)` empties the scan, so the clean assertion goes red and the
guard looks like it noticed something — though the self-test never feeds in a
violation at all.
"""
import sys
from pathlib import Path


def scan(root):
    found = []
    for path in sorted(Path(root).rglob("*.md")):
        found.append(path)
    return found


def judge(root):
    found = scan(root)
    if not found:
        print("什么都没扫到 — 这本身就是失败", file=sys.stderr)
        return 1
    bad = [path.name for path in found if "bad" in path.name]
    return 1 if bad else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return judge(Path(argv[1]) if len(argv) > 1 else Path("."))


def self_test():
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "ok.md").write_text("", encoding="utf-8")
        found = [path.name for path in scan(root)]
        if found != ["ok.md"]:
            print(f"SELF-TEST FAIL: 干净夹具里该扫到 ok.md，得到 {found}", file=sys.stderr)
            return 1
    print("PASS: 合成只查干净夹具的守卫自测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "call-exit-guard.py": '''#!/usr/bin/env python3
"""A guard whose verdict is `raise SystemExit(report(root))`, and whose self-test
only asserts the line `report` prints. Rewriting that argument to `0` would take
the call and the print with it, and the red would prove nothing."""
import subprocess
import sys
import tempfile
from pathlib import Path


def report(root):
    problems = [path.name for path in sorted(Path(root).rglob("*.bad"))]
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if "--self-test" in argv:
        return self_test()
    raise SystemExit(report(Path(argv[1]) if len(argv) > 1 else Path(".")))


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if "修法" not in done.stderr:
            print("SELF-TEST FAIL: 报告里要说出 x.bad", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（报告说出了 x.bad）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "no-self-test-guard.py": '''#!/usr/bin/env python3
"""A guard with no self-test at all."""
import sys


def main():
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "not-green-guard.py": '''#!/usr/bin/env python3
"""A guard whose --self-test has never gone green."""
import sys


def main(argv):
    if "--self-test" in argv:
        print("SELF-TEST FAIL: 这个自测从来没绿过", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "delegating-guard.py": '''#!/usr/bin/env python3
"""A guard whose --self-test runs a sibling test file, in another process."""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return subprocess.call([sys.executable, str(Path(__file__).with_name("test_delegating_guard.py"))])
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "sibling-guard.py": '''#!/usr/bin/env python3
"""A guard with no inline self-test: its check lives in a sibling test file."""
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
}

#: Files the synthetic guards need beside them, but that are not hooks.
SYNTHETIC_SIBLINGS = {
    "test_delegating_guard.py": '''#!/usr/bin/env python3
"""The test file `delegating-guard.py --self-test` hands off to."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import importlib.util

spec = importlib.util.spec_from_file_location(
    "delegating_guard", Path(__file__).with_name("delegating-guard.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def main():
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        (Path(raw) / "x.bad").write_text("", encoding="utf-8")
        if module.violations_in(Path(raw)) != ["x.bad"]:
            print("SELF-TEST FAIL: 本该报出 x.bad", file=sys.stderr)
            return 1
    print("PASS: 委派型守卫的旁置自测")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "test_sibling_guard.py": '''#!/usr/bin/env python3
"""The test file that stands in for `sibling-guard.py --self-test`."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location("sibling_guard", Path(__file__).with_name("sibling-guard.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class SiblingGuardTest(unittest.TestCase):
    def test_reports_a_bad_file(self):
        with tempfile.TemporaryDirectory() as raw:
            Path(raw, "x.bad").write_text("", encoding="utf-8")
            self.assertEqual(module.violations_in(Path(raw)), ["x.bad"])


if __name__ == "__main__":
    unittest.main()
''',
}


def write_synthetic_repo(root: Path) -> Path:
    """Lay down the synthetic guards and a local-hook config that names them."""
    scripts = root / GUARD_DIR
    scripts.mkdir(parents=True, exist_ok=True)
    for name, source in {**SYNTHETIC_GUARDS, **SYNTHETIC_SIBLINGS}.items():
        (scripts / name).write_text(source, encoding="utf-8")
    lines = ["repos:", "  - repo: local", "    hooks:"]
    for name in SYNTHETIC_GUARDS:
        interpreter = "python3" if name.endswith(".py") else "bash"
        lines += [
            f"      - id: {Path(name).stem}",
            f"        entry: {interpreter} {(GUARD_DIR / name).as_posix()}",
            "        language: system",
        ]
    config = root / CONFIG_NAME
    config.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--guard", action="append", default=[], help="只核验这个名字的守卫，可重复")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="单个自测的秒数上限")
    parser.add_argument("--json", action="store_true", help="按 JSON 输出，供机器读")
    parser.add_argument("--self-test", action="store_true", help="证明核验器本身能区分真假自测")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()

    root = args.root.resolve()
    config = (args.config or (root / CONFIG_NAME)).resolve()
    results = evaluate(config, root, args.timeout, set(args.guard) or None)

    if args.json:
        print(
            json.dumps(
                {
                    "guards": [
                        {"hook": hook, "script": path.name, "status": outcome.status, "detail": outcome.detail}
                        for hook, path, outcome, _ in results
                    ]
                },
                ensure_ascii=False,
            )
        )
        in_scope = [item for item in results if item[3]]
        if not in_scope:
            print("没有在 .pre-commit-config.yaml 里找到 .claude/scripts 下的本地守卫", file=sys.stderr)
            unverified = 1
        else:
            findings = sum(1 for item in in_scope if item[2].status in FINDING_STATUSES)
            unverified = sum(1 for item in in_scope if item[2].status == "unverified")
    else:
        findings, unverified = report(results)
    if findings:
        return 1
    if unverified:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
