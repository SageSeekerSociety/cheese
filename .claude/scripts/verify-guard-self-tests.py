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
non-zero `sys.exit` / `raise SystemExit`, and a `return` whose value reaches the
process exit status — `main`'s, and whatever `main` returns straight through
(`return judge(root)`, `code, _ = report(root); return code`). A `return 1` in a
helper that answers a yes/no question — `is_good(line)`, read as
`if not is_good(line)` — never becomes the exit status, and rewriting it would
invert the helper and break a clean fixture: the self-test would go red for the
mutation's own damage, which proves nothing. Collectors
(`findings.append(…)`, `lines.extend(…)`) are left alone for the same reason.
The entry point is left alone on purpose: silencing it would stop the self-test
from running at all, which reads as red without the self-test having noticed
anything. A shell guard's verdict is rewritten the same way: a non-zero `exit`
anywhere (it ends the script) and a non-zero `return` inside a function whose
status is the script's own exit status — the function invoked by the script's
last line, and the commands it hands that status on, including a trailing
`if …; then scan; fi`. `return 1` in a grep-style predicate (`has_bad`, read as
`if has_bad "$1"`) is not a verdict. Only words in command position count: an
`exit 1` that is an argument to `echo`, or written into a record the guard
prints (in quotes or a heredoc), is output and is left as printed. Its whole
`if … SELF_TEST …; then` block is left untouched, along with any helper only
that block calls (`self_fail` defined above it included); a `fi` inside a
heredoc does not end the block, and a `<<EOF` inside quotes opens no heredoc. A
verdict written as a variable (`exit "$CODE"`) offers no literal to rewrite and
is reported as UNVERIFIED rather than guessed at.

A copy that goes green on a *silenced* guard is reported as a finding — unless
the guard hands the work to another script file: a `.py` name that is not its
own in the command position of a spawn *on the self-test path* (the program a
`subprocess`/`os.exec*` call runs, `-m`/`-c` included — a command list held in a
variable is resolved back to its assignment). The path is the named self-test
function, what it calls, and whatever an `if "--self-test"` branch calls, so a
branch that hands off to a helper called `check` is followed as well. There the
mutation never reaches the process that judges. A `.py` the normal scan path
runs is that path's own business and does not make the self-test foreign. Arguments *after* the
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
import builtins
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

#: A heredoc opener. Its body is data — a record the guard prints, a generated
#: sample — so nothing in it is the guard's own verdict.
_HEREDOC_RE = re.compile(r"<<-?\s*(?P<quote>['\"]?)(?P<word>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)")

#: A shell function definition that opens a block: `name() {` on a line of its
#: own. A one-line `name() { …; }` has no body range worth tracking.
_SHELL_FUNCTION_RE = re.compile(r"^(?P<name>[A-Za-z_][\w-]*)\s*\(\)\s*\{\s*(?:#.*)?$")

#: A line that is a bare invocation of one of the guard's functions.
_SHELL_BARE_CALL_RE = re.compile(r"^(?P<name>[A-Za-z_][\w-]*)(?:\s|$)")


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
    """The expression naming the file a spawn call executes, if it runs one."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    return _script_position(node.args[0])


def _script_position(command: ast.expr) -> ast.AST | None:
    """The expression naming the file a spawn command executes, if it runs one.

    Only the program in the command position counts. Everything after it is the
    guard's own command line — a `--root <dir>` and any fixture name ending in
    `.py` are arguments, not scripts — and a non-Python program such as
    `git ls-files '*.py'` runs no script at all. For `python -m` / `python -c`
    the constant itself comes back, since what runs is a module or inline code.
    """
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


def _assigned_commands(tree: ast.Module) -> dict[str, ast.AST]:
    """Command lists bound to a name, so `cmd = [...]` / `subprocess.call(cmd)` resolves.

    Without this the call site sees only a bare name, decides the guard re-runs
    itself, and reports a bite that never happened against a guard whose
    self-test actually lives in the file that variable names.
    """
    assigned: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [target for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        if not isinstance(node.value, (ast.List, ast.Tuple)):
            continue
        for target in targets:
            assigned.setdefault(target.id, node.value)
    return assigned


#: Function names guards give the code their `--self-test` path runs.
_SELF_TEST_FUNCTIONS = frozenset(
    {"self_test", "selftest", "run_self_test", "_self_test", "check_self_test", "run_selftest"}
)


def _mentions_string(node: ast.AST, needle: str) -> bool:
    return any(
        isinstance(sub, ast.Constant) and isinstance(sub.value, str) and needle in sub.value
        for sub in ast.walk(node)
    )


def _branch_runs_self_test(
    test: ast.AST, constants: dict[str, int | None]
) -> bool | None:
    """True when this `if` branch is the one that runs for `--self-test`.

    `if "--self-test" not in argv:` guards the *scan* path; the calls in its body
    are the scan's own, and seeding them would put the scan's helper on the
    self-test path. `None` means the test is about the flag but does not settle
    — the caller then has to keep both sides on the path.
    """
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        inner = _branch_runs_self_test(test.operand, constants)
        return None if inner is None else not inner
    for node in ast.walk(test):
        if isinstance(node, ast.Compare) and _mentions_string(node, "--self-test"):
            return _comparison_holds_with_flag(node, constants)
    return True


def _counts_the_flag(node: ast.AST) -> bool:
    """True for the ways a guard asks how many times the flag appears.

    `argv.count("--self-test")`, `len(argv)`, `sum(...)`: the comparison's
    sense is then about a count, not about membership.
    """
    return isinstance(node, ast.Call) and (
        isinstance(node.func, ast.Name) and node.func.id in ("len", "sum")
        or isinstance(node.func, ast.Attribute) and node.func.attr == "count"
    )


def _comparison_holds_with_flag(
    node: ast.Compare, constants: dict[str, int | None]
) -> bool | None:
    """True when this comparison holds exactly while `--self-test` is passed.

    `"--self-test" in argv` and `argv[1:] == ["--self-test"]` hold with the
    flag; `not in` and `!=` hold without it. A comparison on how many times
    the flag appears inverts that sense for the count of zero — absent — but
    not for any other count: `count("--self-test") == 1` holds exactly when
    the flag is there, the same as `in`. The count compared against may be a
    module-level constant (`NO_FLAG = 0`), so `constants` resolves it.
    """
    operands = [node.left, *node.comparators]
    counted = any(_counts_the_flag(item) for item in operands)
    expected = next(
        (value for value in (_int_value(item, constants) for item in operands if not _counts_the_flag(item)) if value is not None),
        None,
    )
    for op in node.ops:
        if counted and isinstance(op, (ast.Eq, ast.NotEq)):
            if expected is None:
                # The count on the other side is not something this can read.
                # Guessing the self-test side would be wrong the other way: the
                # branch body may be the scan's `return`, and stopping there
                # drops the hand-off below. Say so instead, and let both sides
                # stay on the path.
                return None
            return expected != 0 if isinstance(op, ast.Eq) else expected != 1
        return not isinstance(op, (ast.NotIn, ast.NotEq))
    return True


def _int_value(node: ast.AST, constants: dict[str, int | None]) -> int | None:
    """The integer an operand settles to, or None when it is not an integer literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _int_constants(block: list[ast.stmt]) -> dict[str, int | None]:
    """Names this block assigns an integer literal, resolved at most once.

    The block is one scope's own statements: the module body, or the body of
    the function whose branch reads the name. A name assigned here twice has no
    single value, so it maps to `None` — and it still shadows any outer name of
    the same spelling, which is why `None` is kept rather than dropped.
    """
    seen: dict[str, int | None] = {}
    for node in block:
        if isinstance(node, ast.Assign):
            targets = [target for target in node.targets if isinstance(target, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
        else:
            continue
        value = _int_value(node.value, {}) if node.value is not None else None
        for target in targets:
            seen[target.id] = None if target.id in seen else value
    return seen


def _constants_at(
    node: ast.AST, parents: dict[ast.AST, ast.AST], cache: dict[int, dict[str, int | None]]
) -> dict[str, int | None]:
    """The integer constants a branch reads, each name from the nearest scope assigning it.

    Innermost scope wins, so `argv.count("--self-test") == zero` with a local
    `zero = 0` in the reading function resolves to that local — while the same
    name in some unrelated function stays out of the picture.
    """
    resolved: dict[str, int | None] = {}
    scopes: list[ast.AST] = []
    current: ast.AST | None = node
    while current is not None:
        scopes.append(current)
        current = parents.get(current)
    for scope in reversed(scopes):
        if not isinstance(scope, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        values = cache.get(id(scope))
        if values is None:
            values = _int_constants(scope.body)
            cache[id(scope)] = values
        resolved.update(values)
    return resolved


#: The argv a guard sees when its own `--self-test` runs, as the command in
#: `_self_test_command` builds it. Conditions on it can be settled without
#: running the guard.
_SELF_TEST_ARGV = ["guard.py", "--self-test"]

_UNKNOWN = object()


def _settled(node: ast.AST):
    """The value of a small expression under the `--self-test` invocation.

    Only what that invocation fixes is known: the script name and the flag.
    Anything else — a file read, a platform check, a helper call — is
    `_UNKNOWN`, and a block guarded by it cannot be shown to run.
    """
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name) and node.id == "argv":
        return _SELF_TEST_ARGV
    if (
        isinstance(node, ast.Attribute)
        and node.attr == "argv"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    ):
        # The same command line: `main(sys.argv)` hands `argv` its own value.
        return _SELF_TEST_ARGV
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_settled(item) for item in node.elts]
        if any(item is _UNKNOWN for item in items):
            return _UNKNOWN
        return items
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        value = _settled(node.operand)
        return _UNKNOWN if value is _UNKNOWN else not value
    if isinstance(node, ast.BoolOp):
        values = [_settled(value) for value in node.values]
        if any(value is _UNKNOWN for value in values):
            return _UNKNOWN
        return any(values) if isinstance(node.op, ast.Or) else all(values)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len":
        if len(node.args) == 1 and not node.keywords:
            value = _settled(node.args[0])
            if isinstance(value, (list, tuple, str)):
                return len(value)
        return _UNKNOWN
    if isinstance(node, ast.Compare):
        left = _settled(node.left)
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            right = _settled(comparator)
            if left is _UNKNOWN or right is _UNKNOWN:
                return _UNKNOWN
            try:
                if not _compare(op, left, right):
                    return False
            except TypeError:
                return _UNKNOWN
            left = right
        return True
    return _UNKNOWN


def _compare(op: ast.cmpop, left, right) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.In):
        return left in right
    if isinstance(op, ast.NotIn):
        return left not in right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise TypeError(type(op).__name__)


def _branch_is_proven_reached(branch: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when the `--self-test` invocation provably reaches this branch.

    The walk collects what follows the branch on the assumption that the branch
    is the discriminator the self-test goes through. That is only safe when
    every enclosing statement provably runs: a loop body may run zero times, a
    `match` case may not be the one taken, a handler may never be entered, and
    an `if` whose test this invocation does not settle may go either way.
    """
    node = branch
    while True:
        holder = parents.get(node)
        if holder is None or isinstance(
            holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)
        ):
            return True
        if isinstance(holder, ast.If):
            value = _settled(holder.test)
            if value is _UNKNOWN or bool(value) != (node in holder.body):
                return False
            node = holder
        elif isinstance(holder, (ast.Try, ast.TryStar)):
            if node in holder.body or node in holder.finalbody:
                node = holder
            else:
                return False
        elif isinstance(holder, (ast.With, ast.AsyncWith)):
            node = holder
        else:
            return False


def _exception_name(node: ast.AST) -> str | None:
    """The name an `except` clause says, whether spelled `X` or `mod.X`."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _handler_catches_system_exit(handler: ast.ExceptHandler) -> bool:
    """True when this handler's clause may catch a `SystemExit`.

    A bare `except:` catches everything. `SystemExit` and `BaseException` catch
    it; any other builtin exception does not. A name this module cannot resolve
    might alias `SystemExit`, so it counts: guessing "catches" only makes the
    verdict more conservative, never a finding.
    """
    if handler.type is None:
        return True
    clauses = (
        handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    )
    for clause in clauses:
        name = _exception_name(clause)
        if name is None:
            return True
        builtin = getattr(builtins, name, None)
        if isinstance(builtin, type) and issubclass(builtin, BaseException):
            if issubclass(SystemExit, builtin):
                return True
            continue
        return True
    return False


def _handler_reraises(handler: ast.ExceptHandler) -> bool:
    """True when the handler leaves through a `raise` or `return` of its own.

    Such a handler does not swallow the `SystemExit`: control does not resume
    at the statement after the `try`.
    """
    last = handler.body[-1] if handler.body else None
    return isinstance(last, (ast.Return, ast.Raise))


def _system_exit_is_swallowed(
    statement: ast.stmt, parents: dict[ast.AST, ast.AST]
) -> bool:
    """True when an enclosing `try` catches the `SystemExit` and keeps going.

    `raise SystemExit` / `sys.exit()` are not final on their own for the same
    reason a bare `raise` is not: a `try` whose handler catches `SystemExit`
    and falls through resumes execution after it.
    """
    node: ast.AST = statement
    while True:
        holder = parents.get(node)
        if holder is None or isinstance(
            holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)
        ):
            return False
        if isinstance(holder, (ast.Try, ast.TryStar)) and node in holder.body:
            for handler in holder.handlers:
                if _handler_catches_system_exit(handler) and not _handler_reraises(
                    handler
                ):
                    return True
        node = holder


def _runs_last(statement: ast.stmt) -> ast.stmt | None:
    """The last statement this one really runs, following a settled `if`.

    An `elif` is a nested `if` inside the previous one's `orelse`, and its test
    often settles for the self-test's own command line (`len(sys.argv) > 1`).
    The exit the block ends on lives in that body, so the check for "does this
    block leave the path" has to look there. `None` means the taken side is
    empty and control falls through.
    """
    current: ast.stmt = statement
    while isinstance(current, ast.If):
        value = _settled(current.test)
        if value is _UNKNOWN:
            return current
        side = current.body if value else current.orelse
        if not side:
            return None
        current = side[-1]
    return current


def _caught_handler_body(
    statement: ast.stmt, parents: dict[ast.AST, ast.AST]
) -> list[ast.stmt] | None:
    """The `except` body control enters, when this statement's exit lands there.

    A `try` cannot catch a `return`, but it does catch the `SystemExit` a
    `raise SystemExit` or an `exit` call produces. The handler's body is then on
    the path even when the handler itself returns: the hand-off may sit in the
    return expression, as in `except SystemExit: return check()`.
    """
    statement = _runs_last(statement)
    if statement is None or isinstance(statement, ast.Return):
        return None
    if isinstance(statement, ast.Raise):
        exc = statement.exc
        is_exit = (
            isinstance(exc, ast.Name)
            and exc.id == "SystemExit"
            or isinstance(exc, ast.Call)
            and isinstance(exc.func, ast.Name)
            and exc.func.id == "SystemExit"
        )
    elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        func = statement.value.func
        if isinstance(func, ast.Name):
            is_exit = func.id in ("exit", "quit", "_exit")
        elif isinstance(func, ast.Attribute):
            is_exit = func.attr == "exit"
        else:
            is_exit = False
    else:
        is_exit = False
    if not is_exit:
        return None
    node: ast.AST = statement
    while True:
        holder = parents.get(node)
        if holder is None or isinstance(
            holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.Module)
        ):
            return None
        if isinstance(holder, (ast.Try, ast.TryStar)) and node in holder.body:
            for handler in holder.handlers:
                if _handler_catches_system_exit(handler):
                    return handler.body
        node = holder


def _abandons_the_run(statement: ast.stmt, parents: dict[ast.AST, ast.AST]) -> bool:
    """True when control cannot continue past this statement.

    A `return` ends the path outright. `exit`/`sys.exit` and `raise
    SystemExit` end it too, unless an enclosing `except` catches the
    `SystemExit` and does not re-raise — then execution resumes after the
    `try`. A bare `raise` never counts: whatever it raises, an enclosing
    handler may catch it.
    """
    statement = _runs_last(statement)
    if statement is None:
        return False
    if isinstance(statement, ast.Return):
        return True
    if isinstance(statement, ast.Raise):
        exc = statement.exc
        ends = (
            isinstance(exc, ast.Name)
            and exc.id == "SystemExit"
            or isinstance(exc, ast.Call)
            and isinstance(exc.func, ast.Name)
            and exc.func.id == "SystemExit"
        )
    elif isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Call):
        func = statement.value.func
        if isinstance(func, ast.Name):
            ends = func.id in ("exit", "quit", "_exit")
        elif isinstance(func, ast.Attribute):
            ends = func.attr == "exit"
        else:
            ends = False
    else:
        ends = False
    return ends and not _system_exit_is_swallowed(statement, parents)


def _statement_blocks(tree: ast.Module) -> dict[int, list[ast.stmt]]:
    """Map each statement to the block of statements it sits in.

    `ExceptHandler` and `match_case` have a `body` too, so a statement inside
    an `except` or a `match` case is mapped like any other.
    """
    blocks: dict[int, list[ast.stmt]] = {}
    for node in ast.walk(tree):
        for slot in ("body", "orelse", "finalbody"):
            block = getattr(node, slot, None)
            if isinstance(block, list) and block and isinstance(block[0], ast.stmt):
                for statement in block:
                    blocks[id(statement)] = block
    return blocks


def _self_test_scope(tree: ast.Module) -> list[ast.AST]:
    """The nodes on the `--self-test` path: the self-test function, what it calls,
    and the side of any `if "--self-test" …` branch that runs for the self-test.

    A guard's normal scan may run another `.py` helper quite legitimately; only a
    spawn on the self-test path decides whether mutating this file reaches the
    process that judges it. A branch that hands the work to a helper named
    anything at all — `if "--self-test" in argv: return check()` — puts that
    helper on the path too, so the calls in the branch seed the walk exactly like
    the named self-test functions do.

    A guard that dispatches the other way — `if "--self-test" not in argv: return
    scan()` then `return check()` — keeps its self-test in the statements after
    the branch, so those are on the path as well. The branch may be an `elif`
    or sit inside a `try`, so the walk climbs through its enclosing statements
    until it reaches the function, collecting what follows at each level. It
    climbs for either side, since whichever side ran, control falls through the
    branch unless that side returns, exits or raises `SystemExit`.

    `_abandons_the_run` decides when the climb can stop; a bare `raise` does not
    stop it, because an enclosing `except` may catch it. `_branch_is_proven_reached`
    decides whether the statements after a block that returned are really off the
    path: only when every enclosing statement provably runs for this invocation
    (an `if` whose test on `--self-test`'s argv is true, a `try` body, a `with`)
    is the return final for it.
    """
    named: dict[str, list[ast.AST]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            named.setdefault(node.name, []).append(node)
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
    constant_scopes: dict[int, dict[str, int | None]] = {}
    branches = [
        (node, _branch_runs_self_test(node.test, _constants_at(node, parents, constant_scopes)))
        for node in ast.walk(tree)
        if isinstance(node, ast.If) and _mentions_string(node.test, "--self-test")
    ]
    roots = [
        node
        for name in _SELF_TEST_FUNCTIONS
        for node in named.get(name, [])
    ]
    blocks = _statement_blocks(tree)
    sides: list[list[ast.stmt]] = []
    for branch, for_self_test in branches:
        # The side that runs for the self-test: the branch body when the test
        # holds with the flag, otherwise what follows the branch. When the test
        # is about the flag but does not settle, both sides run in some reading
        # of it: keep both, and never let either end the path.
        # Only a side that provably runs can take the rest of the function off
        # the path. Inside a loop that may run zero times, or under a test this
        # invocation does not settle, the `return` in it is not the end.
        trusted = _branch_is_proven_reached(branch, parents)
        if for_self_test is None:
            trusted = False
        taken = (
            [branch.body, branch.orelse]
            if for_self_test is None
            else [branch.body if for_self_test else branch.orelse]
        )
        for side in taken:
            sides.append(side)
            # An exit caught by an enclosing `except` continues inside the
            # handler, so that body is on the path even when the handler ends
            # by returning — but only if this side really runs.
            if (
                trusted
                and side
                and (landed := _caught_handler_body(side[-1], parents)) is not None
            ):
                sides.append(landed)
        side = taken[0]
        if trusted and side and _abandons_the_run(side[-1], parents):
            continue
        # Whichever side was taken, execution falls through to what follows the
        # branch. The branch may sit in an `elif`, a `try`, a `with`, an
        # `except` or a `match` case, so walk out through its enclosing
        # statements, stopping at the function.
        node: ast.AST = branch
        while True:
            block = blocks.get(id(node))
            if block is None:
                break
            tail = block[block.index(node) + 1 :]
            sides.append(tail)
            # The handler is only entered if this block really runs; collecting
            # it otherwise would put a spawn the self-test never reaches on the
            # path.
            if (
                trusted
                and tail
                and (landed := _caught_handler_body(tail[-1], parents)) is not None
            ):
                sides.append(landed)
            if trusted and tail and _abandons_the_run(tail[-1], parents):
                # The self-test leaves inside a block that provably runs, so
                # nothing further out can be on its path; collecting it would
                # only invent a spawn.
                break
            holder = parents.get(node)
            while isinstance(holder, (ast.ExceptHandler, ast.match_case)):
                holder = parents.get(holder)
            if holder is None or isinstance(
                holder, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Module, ast.Lambda)
            ):
                break
            node = holder
    for side in sides:
        for statement in side:
            for node in ast.walk(statement):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in named
                ):
                    roots.extend(named[node.func.id])
    reachable = {id(node): node for node in roots}
    queue = list(roots)
    while queue:
        current = queue.pop()
        for node in ast.walk(current):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in named
            ):
                for callee in named[node.func.id]:
                    if id(callee) not in reachable:
                        reachable[id(callee)] = callee
                        queue.append(callee)
    scoped: list[ast.AST] = []
    for node in reachable.values():
        scoped.extend(ast.walk(node))
    for branch, _ in branches:
        scoped.extend(ast.walk(branch.test))
    for side in sides:
        for statement in side:
            scoped.extend(ast.walk(statement))
    return scoped


def spawns_foreign_file(tree: ast.Module, own_name: str) -> bool:
    """Does the self-test run another script file, rather than re-running itself?

    A guard that spawns `Path(__file__)` re-runs the copy this tool silences, so
    the mutation travels into the child and the self-test still sees it. One that
    names a different `.py` in the command position — a sibling test file,
    `python -m unittest` — runs the real script in a fresh process where the
    mutation does not exist, so its green says nothing of this copy.
    """
    assigned = _assigned_commands(tree)
    for node in _self_test_scope(tree):
        if not _spawn_target(node):
            continue
        command = node.args[0] if node.args else None
        if isinstance(command, ast.Name) and command.id in assigned:
            command = assigned[command.id]
        if _runs_foreign_script(_script_position(command) if command is not None else None, own_name):
            return True
    return False


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


def _own_nodes(root: ast.AST):
    """Descendants of `root` that are not inside a nested function or class."""
    stack = list(ast.iter_child_nodes(root))
    while stack:
        node = stack.pop()
        yield node
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            stack.extend(ast.iter_child_nodes(node))


def _verdict_return_lines(tree: ast.Module) -> set[int]:
    """Line numbers of `return`s whose value becomes the process exit status.

    A guard reports through `sys.exit(main(...))` / `raise SystemExit(main(...))`,
    so only `main`'s value — and whatever `main` returns straight through — is
    the verdict. A `return 1` in a helper that answers a yes/no question, like
    `is_good(line)` read as `if not is_good(line)`, never reaches the exit
    status; rewriting it breaks the clean path instead of silencing the verdict,
    and the resulting red proves nothing about the self-test.
    """
    functions = {
        node.name: node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    lines: set[int] = set()
    seen: set[str] = set()

    def follow(name: str) -> None:
        if name in seen or name == "self_test":
            return
        seen.add(name)
        function = functions.get(name)
        if function is None:
            return
        returned: set[str] = set()
        for node in _own_nodes(function):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            lines.add(node.lineno)
            value = node.value
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                follow(value.func.id)
            elif isinstance(value, ast.Name):
                returned.add(value.id)
        # `code, _ = report(root); return code` — the code is the verdict, so
        # whatever produced it carries the verdict too.
        for node in _own_nodes(function):
            if not isinstance(node, ast.Assign) or not returned:
                continue
            if not isinstance(node.value, ast.Call) or not isinstance(node.value.func, ast.Name):
                continue
            targets: set[str] = set()
            for target in node.targets:
                if isinstance(target, ast.Name):
                    targets.add(target.id)
                elif isinstance(target, ast.Tuple):
                    targets.update(elt.id for elt in target.elts if isinstance(elt, ast.Name))
            if targets & returned:
                follow(node.value.func.id)

    for node in ast.walk(tree):
        entry = node.exc if isinstance(node, ast.Raise) else node
        found = _exit_arg(entry) if isinstance(entry, ast.Call) else None
        if found is None:
            continue
        arg, _ = found
        if isinstance(arg, ast.Call) and isinstance(arg.func, ast.Name):
            follow(arg.func.id)

    return lines


def _python_edits(
    tree: ast.Module, source: str, skip: set[int]
) -> dict[int, list[tuple[int, int, bytes | None]]]:
    """Byte-slice replacements that turn the guard's failure signals into nothing.

    Only single-line nodes inside a function are rewritten — module level set-up
    is left working, so a copy that dies did so while running, not on import —
    and an outer node chosen for replacement stops the walk so a nested call is
    not also rewritten. What gets rewritten is only where the guard *emits* its
    verdict: a non-zero `sys.exit`/`SystemExit`, and a `return` whose value
    reaches the exit status through `main` (see `_verdict_return_lines`).
    An exit argument that is a call — `raise SystemExit(main(argv))` — keeps
    running and only its result is discarded: replacing it with `0` would delete
    whatever that call printed or wrote, and a red caused by that proves nothing.
    """
    edits: dict[int, list[tuple[int, int, bytes]]] = {}
    skipped = skip | _main_block_lines(tree, source)
    codes = frozenset(_exit_code_names(tree))
    verdicts = _verdict_return_lines(tree)

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
            if (
                isinstance(node, ast.Return)
                and node.value is not None
                and node.lineno in verdicts
            ):
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

    Only the failure verdict is rewritten: a non-zero `exit` — anywhere, since
    it ends the script — and a non-zero `return` inside a function whose status
    is the script's own exit status. Everything else is left alone, including
    `return 1` in a helper that answers a yes/no question (`has_bad`, read as
    `if has_bad "$1"`) and any `exit 1` written into a record it prints. The
    scan runs to the end, so what it prints, records or accumulates still
    happens — a self-test that only looks at those keeps passing, which is
    exactly what has to be reported as toothless. The self-test block itself,
    and any helper only the self-test calls, are left untouched.
    """
    lines = source.splitlines(keepends=True)
    heredocs = _shell_heredoc_lines(lines)
    ranges = _shell_function_ranges(lines)
    block = _shell_self_test_span(lines, heredocs)
    if block is None:
        return None, "找不到 `if … SELF_TEST …; then` 自测块，静音不了守卫的结论"
    owners = _shell_line_owners(lines, ranges)
    verdicts = _shell_verdict_functions(lines, ranges, owners, heredocs, block)
    harness = _shell_harness_functions(lines, ranges, heredocs, block)
    start, end = block
    out: list[str] = []
    count = 0
    for index, line in enumerate(lines):
        if (
            start <= index <= end
            or index in heredocs
            or owners[index] in harness
            or line.lstrip().startswith("#")
        ):
            out.append(line)
            continue
        line, replaced = _silence_shell_line(line, owners[index] in verdicts)
        count += replaced
        out.append(line)
    if count == 0:
        return None, "自测块之外没有非 0 的 exit/return，静音不了守卫的结论"
    return "".join(out), ""


#: Words after which the next word runs as a command, not as an argument.
_SHELL_COMMAND_WORDS = frozenset({"then", "do", "else", "elif", "if", "while", "until", "!", "time", "{"})


def _shell_command_position(line: str, index: int) -> bool:
    """Is the word at `index` a command the shell would run?

    A shell command starts the line (indentation aside), follows a separator
    (`;`, `|`, `&`, `(`, `{`) or follows a control keyword. A word in the middle
    of a simple command is an argument: `echo 修法：让本守卫 exit 1 >&2` prints
    the words, and rewriting that `exit 1` changes the guard's output instead of
    silencing its verdict.
    """
    prefix = line[:index].rstrip(" \t")
    if not prefix:
        return True
    if prefix[-1] in ";|&({":
        return True
    match = re.search(r"([A-Za-z_][\w-]*)$", prefix)
    return match is not None and match.group(1) in _SHELL_COMMAND_WORDS


def _silence_shell_line(line: str, allow_return: bool) -> tuple[str, int]:
    """Rewrite a non-zero `exit` (and, where allowed, `return`) to 0.

    Only a word in command position, outside quotes and comments, counts: a
    verdict written into printed text — `"… exit 1 signals violations"` — is
    output, not an exit status, and rewriting it changed what the guard said.
    """
    out: list[str] = []
    index = 0
    quote = ""
    count = 0
    length = len(line)
    while index < length:
        char = line[index]
        if quote:
            if char == "\\" and quote == '"':
                out.append(line[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
            out.append(char)
            index += 1
            continue
        if char == "\\":
            out.append(line[index : index + 2])
            index += 2
            continue
        if char in "'\"":
            quote = char
            out.append(char)
            index += 1
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t;|&({"):
            out.append(line[index:])
            break
        word = next(
            (candidate for candidate in ("return", "exit") if line.startswith(candidate, index)),
            None,
        )
        at_command = _shell_command_position(line, index)
        if (
            word is not None
            and at_command
            and (word != "return" or allow_return)
            and line[index + len(word) : index + len(word) + 1] in (" ", "\t")
        ):
            after = index + len(word)
            match = re.match(r"[ \t]+([1-9]\d*)(?=\s|;|$)", line[after:])
            if match is not None:
                out.append(word)
                out.append(match.group(0)[: match.start(1)])
                out.append("0")
                index = after + match.end(1)
                count += 1
                continue
        out.append(char)
        index += 1
    return "".join(out), count


def _shell_code_only(line: str) -> str:
    """The line with quoted text and comments blanked out, indices preserved."""
    out = list(line)
    index = 0
    quote = ""
    while index < len(line):
        char = line[index]
        if quote:
            out[index] = " "
            if char == "\\" and quote == '"' and index + 1 < len(line):
                out[index + 1] = " "
                index += 2
                continue
            if char == quote:
                quote = ""
            index += 1
            continue
        if char == "\\":
            out[index] = " "
            if index + 1 < len(line):
                out[index + 1] = " "
            index += 2
            continue
        if char in "'\"":
            quote = char
            out[index] = " "
            index += 1
            continue
        if char == "#" and (index == 0 or line[index - 1] in " \t;|&({"):
            for rest in range(index, len(line)):
                out[rest] = " "
            break
        index += 1
    return "".join(out)


def _shell_heredoc_words(line: str) -> list[str]:
    """Heredoc delimiters opened on this line, outside quotes and comments."""
    words: list[str] = []
    code = _shell_code_only(line)
    for opener in re.finditer(r"<<", code):
        # The delimiter itself may be quoted (`<<'EOF'`), so read it from the
        # raw line at the same offset rather than from the blanked copy.
        raw = _HEREDOC_RE.match(line, opener.start())
        if raw is not None:
            words.append(raw.group("word"))
    return words


def _shell_heredoc_lines(lines: list[str]) -> set[int]:
    """Line indices inside a heredoc body, its terminator included."""
    inside: set[int] = set()
    pending: list[str] = []
    for index, line in enumerate(lines):
        if pending:
            inside.add(index)
            if line.strip() == pending[0]:
                pending.pop(0)
            continue
        if line.lstrip().startswith("#"):
            continue
        pending.extend(_shell_heredoc_words(line))
    return inside


def _shell_function_ranges(lines: list[str]) -> dict[str, tuple[int, int]]:
    """`name() {` … `}` ranges, both ends inclusive, keyed by function name."""
    ranges: dict[str, tuple[int, int]] = {}
    for start, line in enumerate(lines):
        match = _SHELL_FUNCTION_RE.match(line.strip())
        if match is None:
            continue
        name = match.group("name")
        indent = len(line) - len(line.lstrip())
        for candidate in range(start + 1, len(lines)):
            closing = lines[candidate]
            if closing.strip() == "}" and len(closing) - len(closing.lstrip()) == indent:
                ranges[name] = (start, candidate)
                break
    return ranges


def _shell_line_owners(lines: list[str], ranges: dict[str, tuple[int, int]]) -> list[str | None]:
    owners: list[str | None] = [None] * len(lines)
    for name, (start, end) in ranges.items():
        for index in range(start, end + 1):
            owners[index] = name
    return owners


def _bare_call(line: str, names: set[str]) -> str | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    match = _SHELL_BARE_CALL_RE.match(stripped)
    if match is None or match.group("name") not in names:
        return None
    return match.group("name")


def _shell_verdict_functions(
    lines: list[str],
    ranges: dict[str, tuple[int, int]],
    owners: list[str | None],
    heredocs: set[int],
    block: tuple[int, int],
) -> set[str]:
    """Functions whose status is the script's exit status.

    The script's status is its last command's, so only the function invoked by
    that last line — and the commands whose status it hands on — can carry the
    verdict. A helper read as a condition (`if has_bad "$1"`) never does.
    """
    names = set(ranges)
    start, end = block
    verdicts: set[str] = set()
    for index in range(len(lines) - 1, -1, -1):
        if owners[index] is not None or index in heredocs or start <= index <= end:
            continue
        name = _bare_call(lines[index], names)
        if name is not None:
            verdicts.add(name)
            break
    queue = list(verdicts)
    while queue:
        name = queue.pop()
        first, last = ranges[name]
        for callee in _shell_body_calls(lines, first, last, name, owners, heredocs, names):
            if callee not in verdicts:
                verdicts.add(callee)
                queue.append(callee)
    return verdicts


#: Words that end the flow of a function body at its own indentation.
_SHELL_FLOW_ENDS = frozenset({"return", "exit"})


def _shell_body_calls(
    lines: list[str],
    first: int,
    last: int,
    owner: str,
    owners: list[str | None],
    heredocs: set[int],
    names: set[str],
) -> set[str]:
    """Functions whose status can become `owner`'s status.

    Every simple command in the body carries its status unless it is read as a
    condition: `if has_bad "$1"` asks a question, `scan "$1"` hands the answer
    on. Commands after an unconditional `return`/`exit` never run. A trailing
    compound (`if …; then scan "$1"; fi`) counts too: its last command is the
    last thing the function does.
    """
    calls: set[str] = set()
    body_indent = min(
        (len(line) - len(line.lstrip()) for line in lines[first + 1 : last] if line.strip()),
        default=0,
    )
    for index in range(first + 1, last):
        if index in heredocs or owners[index] != owner:
            continue
        stripped = lines[index].strip()
        if not stripped or stripped.startswith("#"):
            continue
        head = stripped.split(None, 1)[0]
        indent = len(lines[index]) - len(lines[index].lstrip())
        if indent == body_indent and head in _SHELL_FLOW_ENDS:
            break
        if head in ("if", "elif", "while", "until"):
            parts = re.split(r"\b(?:then|do)\b", stripped, maxsplit=1)
            stripped = parts[1].strip() if len(parts) > 1 else ""
        callee = _bare_call(stripped, names)
        if callee is not None and callee != owner:
            calls.add(callee)
    return calls


def _shell_harness_functions(
    lines: list[str],
    ranges: dict[str, tuple[int, int]],
    heredocs: set[int],
    block: tuple[int, int],
) -> set[str]:
    """Functions the self-test calls and nothing else does.

    A `self_fail`-style helper defined outside the self-test block belongs to
    the harness, not the guard. Silencing its `exit 1` turns the self-test's own
    failure into a silent success, so a guard that really bites is reported as
    unverified. A function the scan path also calls (`scan`) is not harness.
    """
    start, end = block
    pattern = {name: re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])") for name in ranges}
    inside: set[str] = set()
    outside: set[str] = set()
    for index, line in enumerate(lines):
        if index in heredocs:
            continue
        code = _shell_code_only(line)
        bucket = inside if start <= index <= end else outside
        for name, regex in pattern.items():
            if index == ranges[name][0] or not regex.search(code):
                continue
            bucket.add(name)
    return inside - outside


def _shell_self_test_span(
    lines: list[str], heredocs: set[int] | None = None
) -> tuple[int, int] | None:
    """The line range of the `if … SELF_TEST …; then` block, both ends inclusive.

    The block owns its own failure reporting (`self_fail`, inline `exit 1`), so
    none of it may be rewritten. It ends at the `fi` written at the same
    indentation as its `if`; a `fi` that only appears inside a heredoc — a
    generated sample the self-test writes — is data, not the block's end. A
    missing `fi` runs to the end of the file, where declining to silence
    anything is still honest.
    """
    heredocs = heredocs or set()
    for start, line in enumerate(lines):
        if not _SHELL_SELF_TEST_RE.match(line):
            continue
        indent = len(line) - len(line.lstrip())
        for end in range(start + 1, len(lines)):
            if end in heredocs:
                continue
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
    for _hook, path, outcome, _ in checked:
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
        "shell-heredoc-fi-guard.sh": "ok",
        "shell-trailing-if-guard.sh": "ok",
        "shell-control-word-guard.sh": "ok",
        "shell-dead-code-guard.sh": "unverified",
        "shell-quoted-heredoc-guard.sh": "ok",
        "shell-self-fail-outside-guard.sh": "ok",
        "foreign-spawn-in-scan-guard.py": "ok",
        "fake-green-guard.py": "self-test-does-not-bite",
        "shell-fake-green-guard.sh": "self-test-does-not-bite",
        "clean-only-self-test-guard.py": "self-test-does-not-bite",
        "shell-clean-only-self-test-guard.sh": "self-test-does-not-bite",
        "call-exit-guard.py": "self-test-does-not-bite",
        "predicate-return-guard.py": "self-test-does-not-bite",
        "predicate-return-guard.sh": "self-test-does-not-bite",
        "shell-unquoted-hint-guard.sh": "self-test-does-not-bite",
        "shell-record-text-guard.sh": "self-test-does-not-bite",
        "shell-heredoc-record-guard.sh": "self-test-does-not-bite",
        "no-self-test-guard.py": "missing-self-test",
        "not-green-guard.py": "self-test-not-green",
        "delegating-guard.py": "unverified",
        "sibling-guard.py": "unverified",
        "variable-command-guard.py": "unverified",
        "wrapper-guard.py": "unverified",
        "negative-branch-guard.py": "ok",
        "count-zero-guard.py": "ok",
        "else-handoff-guard.py": "unverified",
        "nested-handoff-guard.py": "unverified",
        "except-handoff-guard.py": "unverified",
        "count-one-handoff-guard.py": "unverified",
        "unreachable-spawn-guard.py": "self-test-does-not-bite",
        "named-count-guard.py": "unverified",
        "counted-tail-guard.py": "unverified",
        "loop-body-branch-guard.py": "unverified",
        "caught-raise-guard.py": "unverified",
        "system-exit-handoff-guard.py": "self-test-does-not-bite",
        "unreached-self-test-branch-guard.py": "unverified",
        "swallowed-system-exit-guard.py": "unverified",
        "raised-exit-guard.py": "self-test-does-not-bite",
        "with-branch-guard.py": "self-test-does-not-bite",
        "match-case-handoff-guard.py": "unverified",
        "annotated-zero-guard.py": "unverified",
        "finally-branch-guard.py": "self-test-does-not-bite",
        "length-guarded-branch-guard.py": "self-test-does-not-bite",
        "shadow-zero-guard.py": "unverified",
        "handler-return-handoff-guard.py": "unverified",
        "handler-return-inside-guard.py": "unverified",
        "handler-return-raised-guard.py": "unverified",
        "unreadable-count-guard.py": "unverified",
        "elif-exit-handler-guard.py": "unverified",
        "sys-argv-branch-guard.py": "self-test-does-not-bite",
        "gated-handler-arm-guard.py": "ok",
        "gated-handler-tail-guard.py": "ok",
        "bare-exit-guard.py": "self-test-does-not-bite",
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
        "元组返回、裸 return、模块级命名常量、SystemExit、SystemExit(调用)、shell 的 exit/return、"
        "经末尾 if 转交的状态；谓词里的 return 1、echo 的参数 exit 1 和打印记录里的 exit 1 都不算结论；"
        "heredoc 里的 fi 不结束自测块、引号里的 <<EOF 不算 heredoc；块外的 self_fail 不动；"
        "参数里的 .py 不算委派、存进变量的命令也会还原、只有自测路径上的外派才判委派、"
        "自测分支调到的普通函数也会跟下去；计数为零的分支是扫描侧、elif 之后照样跟下去；"
        "控制词后面的判定算判定、return 之后的死代码不算；分支落在循环体里不算离开、"
        "被捕获的 raise 不算离开、sys.exit 才算；分支落在 else、match case 里照样跟下去，"
        "落在 with、finally 和已定的条件里才算离开；被 except SystemExit 吞掉的退出不算离开、"
        "处理器再抛出才算；带注解的模块常量也读得出；读的是本函数里的局部常量；"
        "退出落进处理器时处理器的 return 也在路上；裸的 exit 调用同样算离开；"
        "只查干净夹具的假绿被检出（Python 与 shell 各一）、空转假绿被检出、缺自测与没变绿各自报出、"
        "委派给旁置测试文件的判不了）"
    )
    return 0


#: Guards for the self-test: one of each verdict, and one of each shape the
#: failure signal takes (a conditional return, a tuple return, a named code),
#: plus the shapes that must NOT be silenced (a helper's yes/no return, an
#: `exit 1` printed inside a record or a heredoc, a `fi` at column 0 inside
#: a heredoc) and a hand-off whose command is built into a variable.
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
    "predicate-return-guard.py": '''#!/usr/bin/env python3
"""A helper answers yes/no with return codes; only main's return is the verdict."""
import sys
import tempfile
from pathlib import Path

GOOD = "ok"


def is_good(line):
    return 1 if line.strip() == GOOD else 0


def violations_in(root):
    bad = []
    for path in sorted(Path(root).rglob("*.txt")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not is_good(line):
                bad.append(f"{path.name}: {line}")
    return bad


def main(argv):
    if "--self-test" in argv:
        return self_test()
    bad = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for item in bad:
        print(f"{item}: 修法：每行都写成 {GOOD}", file=sys.stderr)
    return 1 if bad else 0


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "a.txt").write_text("ok\\n", encoding="utf-8")
        if violations_in(root) != []:
            print("SELF-TEST FAIL: 干净夹具被报成违规", file=sys.stderr)
            return 1
    print("PASS: 干净夹具")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "predicate-return-guard.sh": '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

has_bad() {
  find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q . && return 0
  return 1
}

scan() {
  if has_bad "$1"; then
    echo "找到 .bad 文件；修法：删掉它" >&2
    exit 1
  fi
  echo "clean"
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  out="$(scan "$tmp" 2>&1)"
  [ "$out" = "clean" ] || { echo "SELF-TEST FAIL: 干净扫描输出 $out" >&2; exit 1; }
  echo "PASS: 干净扫描"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-record-text-guard.sh": '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

count_bad() { find "$1" -name '*.bad' -type f 2>/dev/null | wc -l | tr -d ' '; }

record() {
  local n; n="$(count_bad "$1")"
  printf '{"bad": %s, "hint": "删掉它们；exit 1 表示违规"}\\n' "$n"
}

scan() {
  local n; n="$(count_bad "$1")"
  record "$1"
  if [ "$n" -gt 0 ]; then
    echo "$n 个违规；修法：删掉它们" >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp" || { echo "SELF-TEST FAIL: 干净夹具被拒" >&2; exit 1; }
  rec="$(record "$tmp")"
  want='{"bad": 0, "hint": "删掉它们；exit 1 表示违规"}'
  [ "$rec" = "$want" ] || { echo "SELF-TEST FAIL: 记录是 $rec" >&2; exit 1; }
  echo "PASS: 干净扫描与记录"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-heredoc-record-guard.sh": '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

count_bad() { find "$1" -name '*.bad' -type f 2>/dev/null | wc -l | tr -d ' '; }

record() {
  local n; n="$(count_bad "$1")"
  cat <<EOF
{"bad": $n}
{"hint": "违规会让钩子 exit 1"}
EOF
}

scan() {
  local n; n="$(count_bad "$1")"
  record "$1"
  if [ "$n" -gt 0 ]; then echo "$n 个违规；修法：删掉" >&2; return 1; fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp" || { echo "SELF-TEST FAIL: 干净夹具被拒" >&2; exit 1; }
  rec="$(record "$tmp")"
  want='{"bad": 0}
{"hint": "违规会让钩子 exit 1"}'
  [ "$rec" = "$want" ] || { echo "SELF-TEST FAIL: 记录是 $rec" >&2; exit 1; }
  echo "PASS: 干净扫描与记录"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-heredoc-fi-guard.sh": '''#!/usr/bin/env bash
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

has_bad() { find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q .; }

scan() {
  if has_bad "$1"; then
    echo "找到 .bad 文件；修法：删掉它" >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  cat > "$tmp/sample.sh" <<'EOF'
if [ -n "$x" ]; then
  echo ok
fi
EOF
  scan "$tmp" >/dev/null 2>&1
  [ $? -ne 0 ] || { echo "SELF-TEST FAIL: .bad 文件必须被报出" >&2; exit 1; }
  echo "PASS: .bad 文件被报出"
  exit 0
fi

scan "${1:-.}"
''',
    "variable-command-guard.py": '''#!/usr/bin/env python3
"""The hand-off command is built into a variable before it is run.

The scan path has a real failure signal of its own: without it the copy would
be judged "nothing to silence" whichever way the variable is read, and the
guard would prove nothing about resolving it.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def self_test():
    command = [sys.executable, str(Path(__file__).with_name("test_handoff.py"))]
    code = subprocess.call(command)
    if code != 0:
        print("SELF-TEST FAIL: 旁置测试失败", file=sys.stderr)
        return 1
    print("PASS: 旁置测试跑通了")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "wrapper-guard.py": '''#!/usr/bin/env python3
"""`--self-test` hands off through `check`, a name that is not test-shaped.

The scan path has a real failure signal of its own, so a verdict of "nothing
to silence" cannot be what hides it: only following the branch into `check`
tells the verifier this is a hand-off to a test file.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_wrapper.py"))]
    )


def main(argv):
    if "--self-test" in argv:
        return check()
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "negative-branch-guard.py": '''#!/usr/bin/env python3
"""`if "--self-test" not in argv:` guards the scan; its spawn is the scan's own.

The self-test re-runs this file, so a silenced verdict reaches the process
that judges it. Reading the branch body as the self-test side would call this
guard "unverified" for a helper the self-test never runs.
"""
import subprocess
import sys
from pathlib import Path


def scan(argv):
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    problems = [path.name for path in sorted(Path(argv[1] if len(argv) > 1 else ".").rglob("*.bad"))]
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if "--self-test" not in argv:
        return scan(argv)
    return self_test()


def self_test():
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: .bad 被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "else-handoff-guard.py": '''#!/usr/bin/env python3
"""The hand-off sits after the negative branch, not inside an `else`.

The self-test still lives in `check`, so the copy cannot judge it; only the
statements after the branch put `check` on the self-test path.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_else.py"))]
    )


def main(argv):
    if "--self-test" not in argv:
        return scan(argv)
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "nested-handoff-guard.py": '''#!/usr/bin/env python3
"""The negative branch is an `elif`, so the hand-off is outside its block.

The statements after the branch are still what runs for the self-test; only
the branch's own block is not the function body.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_nested.py"))]
    )


def main(argv):
    if len(argv) > 99:
        return 3
    elif "--self-test" not in argv:
        return scan(argv)
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "count-zero-guard.py": '''#!/usr/bin/env python3
"""The scan is chosen by counting the flag, not by membership.

`argv.count("--self-test") == 0` is the scan side just as `not in` is; reading
it the other way puts the scan's helper on the self-test path.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def main(argv):
    if argv.count("--self-test") == 0:
        return scan(argv)
    return self_test()


def self_test():
    import tempfile

    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: .bad 被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "unreachable-spawn-guard.py": '''#!/usr/bin/env python3
"""The scan-side branch is nested under a condition the self-test settles.

`len(argv) >= 2` holds for `--self-test` — interpreting means the script name
plus the flag — so the branch provably runs, and the self-test returns inside
it. The `helper.py` call after the outer `if` is therefore unreachable for the
self-test, and counting it would only invent a spawn.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def main(argv):
    if len(argv) >= 2:
        if "--self-test" not in argv:
            return scan(argv)
        print("PASS: 什么都没测")
        return 0
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "named-count-guard.py": '''#!/usr/bin/env python3
"""The count is compared against a named zero, not a literal.

`ZERO = 0`, so `count("--self-test") == ZERO` holds when the flag is absent and
the branch is the scan side; the hand-off follows it. Reading the name as an
unreadable count sends the scan branch to the self-test side and drops `check`.
"""
import subprocess
import sys
from pathlib import Path

ZERO = 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == ZERO:
        return 1 if list(Path(argv[1] if len(argv) > 1 else ".").rglob("*.bad")) else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "counted-tail-guard.py": '''#!/usr/bin/env python3
"""The counted flag picks the self-test side, and the branch body falls through.

`count("--self-test") == 1` holds with the flag, so the branch is the self-test
side; it does not return, so what follows the branch runs for the self-test
too, and `check` after it is a real hand-off.
"""
import subprocess
import sys
from pathlib import Path


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == 1:
        print("flag seen")
    if len(argv) > 2:
        return 1
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "loop-body-branch-guard.py": '''#!/usr/bin/env python3
"""The branch sits in a loop body that runs zero times for the self-test.

`roots` is empty when only `--self-test` is passed, so the loop body — and the
`return 0` inside it — is skipped, and execution continues at `check` after the
loop. Reading that `return` as the end of the self-test would miss the hand-off.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    roots = [Path(argument) for argument in argv[1:] if not argument.startswith("-")]
    for root in roots:
        if "--self-test" not in argv:
            return scan(root)
        print("PASS: 什么都没测")
        return 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "caught-raise-guard.py": '''#!/usr/bin/env python3
"""The self-test branch's `raise` is caught, so the hand-off after it still runs.

The `raise` ends the `try` body, but the handler swallows it and execution
resumes at `check`. Treating a bare `raise` as leaving the self-test would drop
that hand-off.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        raise ValueError("boom")
    except ValueError:
        pass
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "system-exit-handoff-guard.py": '''#!/usr/bin/env python3
"""The self-test branch leaves through `sys.exit`, so the hand-off below is dead.

The branch hands nothing off; it prints a PASS line and ends the process. The
`check` after the `try` never runs for `--self-test`, so this guard's green is
false and must be reported as not biting, not as unjudgeable.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        print("PASS: 什么都没测")
        sys.exit(0)
    except SystemExit:
        raise
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "unreached-self-test-branch-guard.py": '''#!/usr/bin/env python3
"""The self-test branch sits under a condition this invocation cannot settle.

`GUARD_SCAN_ROOT` is read from the environment, so the outer block may not run
at all. The `return 0` inside it is not the end of the self-test path, and the
hand-off after the outer block is still reachable.
"""
import os
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    root = os.environ.get("GUARD_SCAN_ROOT")
    if root:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(root))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "swallowed-system-exit-guard.py": '''#!/usr/bin/env python3
"""`sys.exit` in the self-test side is caught, so the hand-off below still runs.

`except SystemExit: pass` swallows the exit and execution resumes at `check`
after the `try`. Reading `sys.exit` as final would drop that hand-off.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        print("PASS: 什么都没测")
        sys.exit(0)
    except SystemExit:
        pass
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "raised-exit-guard.py": '''#!/usr/bin/env python3
"""The self-test side raises `SystemExit`, and the handler re-raises it.

The re-raise carries the exit out of the `try`, so the `check` after it never
runs for `--self-test` and this guard's green is false.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        print("PASS: 什么都没测")
        raise SystemExit(0)
    except SystemExit:
        raise
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "with-branch-guard.py": '''#!/usr/bin/env python3
"""The self-test branch sits in a `with` body, so its `return` is final.

A `with` body provably runs, so the `return 0` inside it takes the `check`
below the `with` off the self-test path; this guard's green never tested it.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    with open(__file__, encoding="utf-8") as stream:
        stream.read(1)
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "match-case-handoff-guard.py": '''#!/usr/bin/env python3
"""The self-test branch sits in a `match` case that falls through to a hand-off.

The case prints and leaves the `match`, then `check` runs. Stopping the climb
at the case instead of climbing out through the `match` drops that hand-off.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    match argv[1:]:
        case [first]:
            print(f"scanning {first}")
            if "--self-test" not in argv:
                return 1 if violations_in(first) else 0
            print("PASS: 什么都没测")
        case _:
            print("no root")
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "finally-branch-guard.py": '''#!/usr/bin/env python3
"""The self-test branch sits in a `finally` body, which always runs.

Its `return 0` leaves through the `finally`, so the `check` after the `try` is
dead for `--self-test` and this guard's green never tested it.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        pass
    finally:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "length-guarded-branch-guard.py": '''#!/usr/bin/env python3
"""The self-test branch sits under `len(argv) > 1`, which holds for the self-test.

`["guard.py", "--self-test"]` is two long, so the outer block provably runs and
the `return 0` inside it takes the `check` below off the self-test path.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if len(argv) > 1:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            return 0
        return scan(Path(argv[1]))
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "annotated-zero-guard.py": '''#!/usr/bin/env python3
"""The zero is a module constant with a type annotation: `ZERO: int = 0`.

`count("--self-test") == ZERO` holds when the flag is absent and the branch is
the scan side; the hand-off follows it. Failing to read the annotated name
sends the scan branch to the self-test side and drops `check`.
"""
import subprocess
import sys
from pathlib import Path

ZERO: int = 0


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == ZERO:
        return 1 if violations_in(argv[1] if len(argv) > 1 else ".") else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "count-one-handoff-guard.py": '''#!/usr/bin/env python3
"""`count("--self-test") == 1` holds when the flag *is* there; it is a hand-off.

Only a count of zero means the flag is absent, so only `== 0` is the scan.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == 1:
        return check()
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "except-handoff-guard.py": '''#!/usr/bin/env python3
"""The branch sits in a handler that always runs; the hand-off is after the try.

The self-test side falls out of the handler and reaches `check` below it, so
the walk has to climb out of the `except` body; reading the handler as a dead
end would leave the hand-off out of scope.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(argv):
    return 1 if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_except.py"))]
    )


def main(argv):
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        if "--self-test" not in argv:
            return scan(argv)
        print("running the self-test")
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "shell-unquoted-hint-guard.sh": '''#!/usr/bin/env bash
# `echo … exit 1` prints words; the self-test only looks at that text.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  if find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q .; then
    echo 修法：让本守卫 exit 1 >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  out="$(scan "$tmp" 2>&1)"
  case "$out" in
    *"exit 1"*) ;;
    *) echo "SELF-TEST FAIL: 提示里要写出 exit 1" >&2; exit 1 ;;
  esac
  echo "PASS: 提示写出了 exit 1"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-trailing-if-guard.sh": '''#!/usr/bin/env bash
# The status comes back through `run`, which ends on an `if`.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q . || return 0
  echo "找到 .bad 文件；修法：删掉它" >&2
  return 1
}

run() {
  if [ -e "$1" ]; then
    if [ -n "$1" ]; then
      scan "$1"
    fi
  fi
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  if run "$tmp"; then
    echo "SELF-TEST FAIL: .bad 文件必须让 run 返回非 0" >&2
    exit 1
  fi
  echo "PASS: .bad 文件被报出"
  exit 0
fi

run "${1:-.}"
''',
    "shell-control-word-guard.sh": '''#!/usr/bin/env bash
# The verdict sits right after `then`, on the same line.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  echo "找到 .bad；修法：删掉" >&2
  if find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q .; then return 1; fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp" && { echo "SELF-TEST FAIL: .bad 必须被报出" >&2; exit 1; }
  echo "PASS: .bad 被报出"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-dead-code-guard.sh": '''#!/usr/bin/env bash
# The only non-zero signal sits after `return 0` and never runs.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

dead() {
  return 1
}

scan() {
  echo clean
  return 0
  dead "$1"
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  scan "$tmp"
  echo "PASS: 干净目录不报错"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-quoted-heredoc-guard.sh": '''#!/usr/bin/env bash
# `grep '<<EOF'` searches for the text; it opens no heredoc.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

scan() {
  if grep -rn '<<EOF' "$1" >/dev/null 2>&1; then
    echo "找到 heredoc 用法；修法：改用 printf" >&2
    return 1
  fi
  return 0
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  printf '%s\\n' 'cat <<EOF' > "$tmp/a.sh"
  scan "$tmp" && { echo "SELF-TEST FAIL: heredoc 用法必须被报出" >&2; exit 1; }
  echo "PASS: heredoc 用法被报出"
  exit 0
fi

scan "${1:-.}"
''',
    "shell-self-fail-outside-guard.sh": '''#!/usr/bin/env bash
# `self_fail` is defined outside the self-test block but belongs to the harness.
set -u
SELF_TEST=0
[ "${1:-}" = "--self-test" ] && { SELF_TEST=1; shift; }

self_fail() {
  echo "SELF-TEST FAIL: $*" >&2
  exit 1
}

scan() {
  find "$1" -name '*.bad' -print -quit 2>/dev/null | grep -q . || return 0
  echo "找到 .bad；修法：删掉" >&2
  return 1
}

if [ "$SELF_TEST" = 1 ]; then
  tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
  : > "$tmp/x.bad"
  scan "$tmp" && self_fail "一个 .bad 文件必须报错"
  echo "PASS: .bad 被报出"
  exit 0
fi

scan "${1:-.}"
''',
    "foreign-spawn-in-scan-guard.py": '''#!/usr/bin/env python3
"""The scan path runs helper.py; the self-test re-runs this file."""
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def main(argv):
    if "--self-test" in argv:
        return self_test()
    subprocess.run([sys.executable, str(Path(__file__).with_name("helper.py"))], check=False)
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉它", file=sys.stderr)
    return 1 if problems else 0


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1:
            print("SELF-TEST FAIL: 没有以 1 报出 .bad", file=sys.stderr)
            return 1
    print("PASS: .bad 被报出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
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
    "shadow-zero-guard.py": '''#!/usr/bin/env python3
"""The zero is a local in the same function that reads it: `zero = 0`.

`count("--self-test") == zero` is the scan side and `check` follows it. Looking
only at module-level names misses this local, sends the scan branch to the
self-test side and drops the hand-off.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    zero = 0
    if argv.count("--self-test") == zero:
        return 1 if violations_in(argv[1] if len(argv) > 1 else ".") else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "handler-return-handoff-guard.py": '''#!/usr/bin/env python3
"""The hand-off is in the `except` body's return: `except SystemExit: return check()`.

The self-test side exits, the handler catches it, and the handler's own return
expression runs the sibling test. Treating a returning handler as an outright
end drops `check` and reports a hand-off that is really there.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        print("PASS: 什么都没测")
        sys.exit(0)
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "handler-return-inside-guard.py": '''#!/usr/bin/env python3
"""The self-test branch's own body exits, and the handler returns the hand-off.

`exit(0)` ends the branch side; the enclosing handler catches it and runs the
sibling from its return expression. The branch side is where control leaves, so
that is where the handler it lands in has to be picked up.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            exit(0)
        else:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "handler-return-raised-guard.py": '''#!/usr/bin/env python3
"""The branch side leaves by `raise SystemExit(0)`, and the handler returns the hand-off.

The `raise` form of the exit has to land in the handler just as an `exit` call
does, or the sibling run from the handler's return is dropped.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" in argv:
            print("PASS: 什么都没测")
            raise SystemExit(0)
        else:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "unreadable-count-guard.py": '''#!/usr/bin/env python3
"""The count is compared against `LIMIT`, whose value cannot be read.

`1 == LIMIT` is false at runtime, so the self-test really does fall through to
the hand-off at the sibling test. Guessing that the self-test side is the
branch body stops the walk there and calls that hand-off toothless.
"""
import os
import subprocess
import sys
from pathlib import Path

LIMIT = int(os.environ.get("LIMIT", "0"))


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if argv.count("--self-test") == LIMIT:
        print("PASS: 什么都没测")
        return 1 if violations_in(argv[1] if len(argv) > 1 else ".") else 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "gated-handler-arm-guard.py": '''#!/usr/bin/env python3
"""The branch side exits into a handler, but under a gate this cannot settle.

The inner `if "--self-test" in argv:` sits under `os.environ.get("MODE")`, so
its `sys.exit(0)` is not shown to run and the handler below it — which spawns a
sibling test — is not on the path. The self-test that does run is the guard's
own, and silencing it turns the run red.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1 or "修法" not in done.stderr:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（.bad 文件被报出并给了修法）")
    return 0


def scan(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if os.environ.get("MODE") == "1":
        try:
            if "--self-test" in argv:
                print("PASS: 什么都没测")
                sys.exit(0)
        except SystemExit:
            return subprocess.call(
                [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
            )
    return scan(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "gated-handler-tail-guard.py": '''#!/usr/bin/env python3
"""The exit after the branch is caught by a handler, but under a gate.

Everything here sits under `os.environ.get("MODE")`, which this tool cannot
settle: the `sys.exit(0)` that follows the flag test is not shown to run, so
the handler that spawns a sibling test is not on the path either. What runs is
the guard's own self-test, and silencing it turns the run red.
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def self_test():
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        (root / "x.bad").write_text("", encoding="utf-8")
        done = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), str(root)],
            capture_output=True,
            text=True,
        )
        if done.returncode != 1 or "修法" not in done.stderr:
            print(f"SELF-TEST FAIL: 本该以 1 报出 x.bad，得到 {done.returncode}", file=sys.stderr)
            return 1
    print("PASS: 合成守卫自测（.bad 文件被报出并给了修法）")
    return 0


def scan(argv):
    if "--self-test" in argv:
        return self_test()
    problems = violations_in(Path(argv[1]) if len(argv) > 1 else Path("."))
    for name in problems:
        print(f"{name}: 修法：删掉这个文件", file=sys.stderr)
    return 1 if problems else 0


def main(argv):
    if os.environ.get("MODE") == "1":
        try:
            if "--self-test" not in argv:
                return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
            print("PASS: 什么都没测")
            sys.exit(0)
        except SystemExit:
            return subprocess.call(
                [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
            )
    return scan(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "elif-exit-handler-guard.py": '''#!/usr/bin/env python3
"""The exit sits in an `elif` body and the handler returns the hand-off.

`len(sys.argv) > 1` holds for the self-test's own command line, so the `elif`
body runs and the `sys.exit(0)` in it lands in `except SystemExit`, whose
return expression runs the sibling test. Only looking at the first `if`'s own
statements misses the exit and reports the hand-off as dead.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" not in argv:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
        elif len(sys.argv) > 1:
            print("PASS: 什么都没测")
            sys.exit(0)
        else:
            return 0
    except SystemExit:
        return check()
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "sys-argv-branch-guard.py": '''#!/usr/bin/env python3
"""The inner exit is guarded by `len(sys.argv) > 1`, which holds here.

`main(sys.argv)` hands the function the same command line, so the inner block
provably runs for `--self-test` and its `sys.exit(0)` ends the run: the
`except SystemExit: raise` re-raises it, and `check` below is dead. Failing to
read `sys.argv` would keep that hand-off on the path.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def scan(root):
    return 1 if violations_in(root) else 0


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    try:
        if "--self-test" in argv:
            if len(sys.argv) > 1:
                print("PASS: 什么都没测")
                sys.exit(0)
        else:
            return scan(Path(argv[1]) if len(argv) > 1 else Path("."))
    except SystemExit:
        raise
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
    "bare-exit-guard.py": '''#!/usr/bin/env python3
"""The self-test leaves through the bare builtin `exit(0)`, not `sys.exit`.

The `check` below is dead for `--self-test`, so this guard's green is false.
Reading only `sys.exit` misses the builtin and keeps the hand-off on the path.
"""
import subprocess
import sys
from pathlib import Path


def violations_in(root):
    return [path.name for path in sorted(Path(root).rglob("*.bad"))]


def check():
    return subprocess.call(
        [sys.executable, str(Path(__file__).with_name("test_counted.py"))]
    )


def main(argv):
    if "--self-test" in argv:
        print("PASS: 什么都没测")
        exit(0)
    if violations_in(Path(argv[1]) if len(argv) > 1 else Path(".")):
        return 1
    return check()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
''',
}

#: Files the synthetic guards need beside them, but that are not hooks.
SYNTHETIC_SIBLINGS = {
    "helper.py": '''#!/usr/bin/env python3
"""A helper the scan path runs; not a test file."""
print("helper")
''',
    "test_handoff.py": '''#!/usr/bin/env python3
"""The test file `variable-command-guard.py --self-test` hands off to."""
import unittest


class HandoffTest(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
''',
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
    "test_counted.py": '''#!/usr/bin/env python3
"""The test file `count-one-handoff-guard.py --self-test` hands off to."""
print("PASS: 旁置测试跑通了")
''',
    "test_except.py": '''#!/usr/bin/env python3
"""The test file `except-handoff-guard.py --self-test` hands off to."""
print("PASS: 旁置测试跑通了")
''',
    "test_nested.py": '''#!/usr/bin/env python3
"""The test file `nested-handoff-guard.py --self-test` hands off to."""
print("PASS: 旁置测试跑通了")
''',
    "test_else.py": '''#!/usr/bin/env python3
"""The test file `else-handoff-guard.py --self-test` hands off to."""
print("PASS: 旁置测试跑通了")
''',
    "test_wrapper.py": '''#!/usr/bin/env python3
"""The test file `wrapper-guard.py --self-test` hands off to through `check`."""
print("PASS: 旁置测试跑通了")
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
