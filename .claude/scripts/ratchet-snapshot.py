#!/usr/bin/env python3
"""Collect one ratchet snapshot: what every check said about one commit.

    python3 .claude/scripts/ratchet-snapshot.py --out ratchet-snapshot.json
    python3 .claude/scripts/ratchet-snapshot.py --self-test

Each check is run as a process, the way CI runs it, and its `--json` record is
copied into the snapshot with two things added that only this script knows: the
area it belongs to, and the fingerprint of the files that decide it. Nothing is
recomputed and nothing is judged here — a snapshot that disagreed with the gate
would be a second opinion nobody asked for.

A CHECK THAT DID NOT RUN IS `not_collected`, NEVER ZERO. The distinct outcomes
are worth keeping apart: `pass`/`fail`/`cannot_judge` come from the checker's own
exit code (0/1/2), and a check that produced no record at all — its command is
missing, it timed out, or it decided this tree was not its business — is
`not_collected` with the reason. A zero would read as "measured, nothing wrong",
which is the one thing this arrangement exists to prevent.

The exit code is not a verdict. This runs after a merge, and a snapshot is
wanted even when a check is red, so the run exits 0 whenever it wrote one.

A run that could not collect still leaves a file: the snapshot, or a record
saying the collection failed and why. A series with a hole in it cannot tell a
failed run from a run nobody made, which is the same mistake as a zero for a
check that did not run, one step earlier. Only such a failed collection exits 2.

The record's shape is fixed in docs/topics/棘轮页方案 section 3.1.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Bumped when the shape of a snapshot changes in a way a reader must know about.
SNAPSHOT_VERSION = 1

#: The four areas the page is divided into. A check belongs to exactly one.
SCENES = "场景"
BOUNDARY = "边界"
SIZE = "规模"
TYPES = "类型与样式"

#: How long one check may take. The frontend checks are the slow ones (a few
#: minutes on a cold checkout); a check still running after this did not fail,
#: it never finished, and the snapshot says so.
CHECK_TIMEOUT = 1800


@dataclass(frozen=True)
class Check:
    """One check, its area, and the files whose bytes decide it."""

    id: str
    area: str
    argv: tuple[str, ...]
    cwd: str = "."
    rules: tuple[str, ...] = ()
    #: `ignore_imports` for a file whose baseline lives beside its rules.
    strip: str | None = None
    #: A pytest ledger's table (`_EXEMPT`, `_LEDGER`, `BASELINE`) lives inside
    #: the module that is also its rule. Naming it here cuts it out of the
    #: fingerprint, because paying an entry off is the debt payment the page
    #: counts — without this, dropping one exemption reads as a rule change.
    baseline: str | None = None
    #: (path under cwd, expression) for a pytest ledger: how much it registers.
    #: Read by importing the module and evaluating the expression in its
    #: namespace — the ledger IS the number, and the test asserts the tree still
    #: matches it. An expression rather than an attribute name because a
    #: ledger's unit is not always its length: `BASELINE` in the is_private
    #: check maps each file to HOW MANY read points it holds, so those 12 files
    #: are 20 read points and `len()` would report the files instead.
    probe: tuple[str, str] | None = None
    #: True for a check whose verdict comes from a test run rather than a record.
    ledger: bool = False
    timeout: int = field(default=CHECK_TIMEOUT)


#: Every check the page shows, in the order it shows them. A check missing from
#: this list is a check the page silently stops showing, so this list is the
#: inventory the design's four areas were agreed on.
CHECKS: tuple[Check, ...] = (
    Check(
        id="scene-ratchet",
        area=SCENES,
        argv=("python", ".claude/scripts/scene-ratchet.py", "--json"),
        rules=(
            ".claude/scripts/scene-ratchet.py",
            ".claude/scripts/frontend_grade.py",
            ".claude/scripts/ratchet_report.py",
        ),
    ),
    *(Check(
        id=check_id,
        area=SCENES if kind != "network" else BOUNDARY,
        argv=("python", ".claude/scripts/scene-debt-ratchet.py", "--kind", kind, "--json"),
        rules=(
            ".claude/scripts/scene-debt-ratchet.py",
            ".claude/scripts/test_scene_debt_ratchet.py",
            ".claude/scripts/scene-ratchet.py",
            ".claude/scripts/frontend_grade.py",
            ".claude/scripts/ratchet_report.py",
            "frontend/scripts/scene-debt-scan.mjs",
            "frontend/package.json",
            "frontend/pnpm-lock.yaml",
        ),
    ) for kind, check_id in (
        ("children", "scene-non-a-children"),
        ("routes", "scene-use-route"),
        ("network", "legacy-network"),
    )),
    Check(
        id="catalog-ratchet",
        area=SCENES,
        argv=("python", ".claude/scripts/catalog-ratchet.py", "--json"),
        rules=(
            ".claude/scripts/catalog-ratchet.py",
            ".claude/scripts/frontend_grade.py",
            ".claude/scripts/ratchet_report.py",
        ),
    ),
    Check(
        id="fe-boundary",
        area=BOUNDARY,
        argv=("node", "scripts/import-boundary-ratchet.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/eslint.boundary.config.mjs",
            # The boundary config builds on the base one; a rule added there
            # decides this check too.
            "frontend/eslint.config.mjs",
            "frontend/scripts/import-boundary-ratchet.mjs",
            "frontend/scripts/import-boundary-ratchet-core.mjs",
            # Shared with the tsc and stylelint ratchets: `compare()` and
            # `tightenedBaseline()` are what turns counts into a verdict.
            "frontend/scripts/tsc-ratchet-core.mjs",
            # BETTER and the status mapping both live here, for all three.
            "frontend/scripts/ratchet-report.mjs",
        ),
    ),
    Check(
        id="be-contracts",
        area=BOUNDARY,
        argv=("python", "scripts/check_boundaries.py", "--json"),
        cwd="backend",
        rules=(
            "backend/.importlinter",
            # The checker itself is where the verdict is decided: which
            # contracts run, and what counts as a violation.
            "backend/scripts/check_boundaries.py",
        ),
        strip="ignore_imports",
    ),
    Check(
        id="be-deferred-imports",
        area=BOUNDARY,
        argv=("python", "scripts/check_deferred_imports.py", "--json"),
        cwd="backend",
        # The counting, the annotation syntax and the verdict all live in the
        # script; the per-file allowances are its baseline, not its rule.
        rules=("backend/scripts/check_deferred_imports.py",),
    ),
    Check(
        id="domain-import-guard",
        area=BOUNDARY,
        argv=("python", "-m", "pytest", "tests/unit/test_domain_import_guard.py", "-q"),
        cwd="backend",
        rules=("backend/tests/unit/test_domain_import_guard.py",),
        probe=("tests/unit/test_domain_import_guard.py", "len(_EXEMPT)"),
        baseline="_EXEMPT",
        ledger=True,
    ),
    Check(
        id="harness-boundary",
        area=BOUNDARY,
        argv=("python", "-m", "pytest", "tests/unit/test_harness_boundary.py", "-q"),
        cwd="backend",
        rules=("backend/tests/unit/test_harness_boundary.py",),
        probe=("tests/unit/test_harness_boundary.py", "len(_LEDGER)"),
        baseline="_LEDGER",
        ledger=True,
    ),
    Check(
        id="is-private-read-points",
        area=BOUNDARY,
        argv=("python", "-m", "pytest", "tests/unit/test_is_private_read_points.py", "-q"),
        cwd="backend",
        rules=("backend/tests/unit/test_is_private_read_points.py",),
        probe=("tests/unit/test_is_private_read_points.py", "sum(BASELINE.values())"),
        baseline="BASELINE",
        ledger=True,
    ),
    Check(
        id="file-sizes",
        area=SIZE,
        argv=("python", ".claude/scripts/check-file-sizes.py", "--json", "--base", "origin/main"),
        rules=(".claude/scripts/check-file-sizes.py", ".claude/scripts/ratchet_report.py"),
    ),
    Check(
        id="vue-tsc",
        area=TYPES,
        argv=("node", "scripts/tsc-ratchet.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/scripts/tsc-ratchet.mjs",
            "frontend/scripts/tsc-ratchet-core.mjs",
            "frontend/scripts/ratchet-report.mjs",
            "frontend/tsconfig.json",
            "frontend/tsconfig.app.json",
        ),
    ),
    Check(
        id="stylelint-tokens",
        area=TYPES,
        argv=("node", "scripts/stylelint-ratchet.mjs", "--json"),
        cwd="frontend",
        rules=(
            "frontend/stylelint.config.cjs",
            "frontend/scripts/stylelint-ratchet.mjs",
            "frontend/scripts/stylelint-ratchet-core.mjs",
            "frontend/scripts/tsc-ratchet-core.mjs",
            "frontend/scripts/ratchet-report.mjs",
        ),
    ),
    Check(
        id="palette",
        area=TYPES,
        argv=("bash", ".claude/scripts/check-repo-rules.sh", "--json"),
        rules=(".claude/scripts/check-repo-rules.sh",),
    ),
)

#: The board: arch-metrics.py's own output, stored as it wrote it. Reference
#: metrics, not a gate — the page reads the size area's tree-wide numbers from
#: here, because the size check is diff-scoped by design and has nothing to
#: count on a tree nobody changed.
BOARD_ARGV: tuple[str, ...] = ("python", ".claude/scripts/arch-metrics.py", "--quiet")

STATUS_OF_EXIT = {0: "pass", 1: "fail", 2: "cannot_judge"}


# ---------------------------------------------------------------------------
# Running one check
# ---------------------------------------------------------------------------


def _binary(name: str) -> str:
    """`python` is this interpreter, whatever it was launched as."""
    return sys.executable if name == "python" else name


def parse_record(stdout: str) -> dict | None:
    """The record a checker printed, or None if it printed something else.

    One line, one object: a checker that printed two lines did not answer the
    question this collector asked, and taking the first would hide that.
    """
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        return None
    try:
        record = json.loads(lines[0])
    except json.JSONDecodeError:
        return None
    return record if isinstance(record, dict) else None


def _not_collected(check: Check, reason: str) -> dict:
    return {
        "id": check.id,
        "area": check.area,
        "status": "not_collected",
        "actual": None,
        "frozen": None,
        "stale": [],
        "details": [],
        "better": "down",
        "reason": reason.strip()[:2000],
    }


def _probe(check: Check, cwd: Path) -> int | None:
    """How much a pytest ledger registers, or None if it cannot be read.

    Importing the test module is the cheapest way to read a registry that lives
    in it. None rather than 0 whenever it fails: a ledger nobody could read is
    not an empty ledger.
    """
    if check.probe is None:
        return None
    path, expression = check.probe
    module = Path(path).stem
    code = (
        "import sys; from pathlib import Path; "
        f"sys.path[:0] = ['.', str(Path({path!r}).parent.absolute())]; "
        f"import {module} as m; print(eval({expression!r}, vars(m)))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", code], cwd=cwd, capture_output=True, text=True, timeout=120
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip())
    except ValueError:
        return None


def _ledger_record(check: Check, code: int, stdout: str, stderr: str, cwd: Path) -> dict:
    """The record for a check whose verdict is a test run.

    pytest's own codes are not this script's: 1 is a failed test, and anything
    above it (2 interrupted, 3 internal error, 4 usage, 5 nothing collected) is
    a run that did not judge the tree — `cannot_judge`, never a pass.
    """
    registered = _probe(check, cwd)
    if code not in STATUS_OF_EXIT:
        status = "cannot_judge"
        reason = f"pytest exited {code}, which is not a verdict: {_tail(stderr or stdout)}"
    else:
        status = STATUS_OF_EXIT[code]
        reason = "" if status == "pass" else _tail(stderr or stdout)
    return {
        "id": check.id,
        "area": check.area,
        "status": status,
        # The ledger is the registry: the test passing is the tree matching it.
        # When it fails, how far the tree is from the registry is not a number
        # this collector has, and null says so.
        "actual": registered if status == "pass" else None,
        "frozen": registered,
        "stale": [],
        "details": [],
        "better": "down",
        "reason": reason,
    }


def _tail(text: str, lines: int = 3) -> str:
    kept = [line for line in text.strip().splitlines() if line.strip()]
    return "\n".join(kept[-lines:])


def _runnable(cwd: Path, module: str) -> bool:
    """Whether this interpreter can import `module` at all.

    Needed because `python -m pytest` with pytest missing exits 1 — the same
    code a failed test exits with. Without this, a checkout whose test
    dependencies were never installed would record three red checks instead of
    three checks that did not run.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", f"import {module}"],
            cwd=cwd,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def run_check(check: Check, root: Path) -> dict:
    cwd = (root / check.cwd).resolve()
    if not cwd.is_dir():
        return _not_collected(check, f"{check.cwd}/ is not in this checkout")
    if check.ledger and not _runnable(cwd, "pytest"):
        return _not_collected(check, f"pytest is not installed in {sys.executable}")
    argv = [_binary(part) for part in check.argv]
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=check.timeout,
        )
    except FileNotFoundError as exc:
        return _not_collected(check, f"{exc}")
    except subprocess.TimeoutExpired:
        return _not_collected(check, f"still running after {check.timeout}s")
    except OSError as exc:
        return _not_collected(check, f"could not run {argv[0]}: {exc}")

    if check.ledger:
        return _ledger_record(check, result.returncode, result.stdout, result.stderr, cwd)

    record = parse_record(result.stdout)
    if record is None:
        return _not_collected(
            check,
            "the check emitted no record "
            f"(exit {result.returncode}): {_tail(result.stderr) or result.stdout.strip()[:200]}",
        )
    if record.get("status") not in (*STATUS_OF_EXIT.values(), "not_collected"):
        return _not_collected(check, f"the record's status is {record.get('status')!r}")
    if record["status"] == "not_collected":
        return _not_collected(check, record.get("reason") or "the check said this tree was not its business")
    # The exit code is the authority on the verdict, as it is everywhere else in
    # this arrangement. A record that disagrees with it is not a second witness
    # but a contradiction — and the answer to a contradiction is never the
    # friendlier of the two. Recording what the record claimed (a pass, say, over
    # a process that exited 2) is how a tree nobody could judge goes green.
    expected = STATUS_OF_EXIT.get(result.returncode)
    if expected != record["status"]:
        return _not_collected(
            check,
            f"the exit code and the record disagree: exit {result.returncode} is "
            f"{expected or 'not a verdict'}, the record says {record['status']!r}",
        )
    record.setdefault("actual", None)
    record.setdefault("frozen", None)
    record.setdefault("stale", [])
    record.setdefault("details", [])
    record.setdefault("better", "down")
    record.setdefault("reason", _tail(result.stderr) if record["status"] != "pass" else "")
    return record


# ---------------------------------------------------------------------------
# Rule fingerprints: has the rule moved, or the tree?
# ---------------------------------------------------------------------------


def _strip_ignore_imports(text: str) -> bytes:
    """`.importlinter` with every `ignore_imports` block removed.

    The baseline lives in the same file as the contracts there, so a fingerprint
    over the whole file would call every exemption change a change of rule. It
    is not: the rules are the contract declarations.
    """
    kept: list[str] = []
    skipping = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if not skipping and stripped.startswith("ignore_imports"):
            skipping = True
            continue
        if skipping:
            if line[:1].isspace() and stripped:
                continue
            skipping = False
        kept.append(line)
    return "".join(kept).encode("utf-8")


def _strip_baseline_table(text: str, name: str) -> bytes:
    """`text` with the top-level `name = ...` statement removed.

    `_EXEMPT`, `_LEDGER` and `BASELINE` live inside the modules that also hold
    the rules, so a fingerprint over the whole file would call every exemption
    change a change of rule — and the exemption that just disappeared is the
    debt payment the page is supposed to show, not a new ruler. Parsed with
    `ast` rather than pattern-matched: where the statement ends is a question
    the language answers exactly and a bracket counter only guesses.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text.encode("utf-8")
    lines = text.splitlines(keepends=True)
    for node in tree.body:
        target = node.targets[0] if isinstance(node, ast.Assign) and len(node.targets) == 1 else None
        if isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name) and target.id == name and node.end_lineno:
            for index in range(node.lineno - 1, node.end_lineno):
                lines[index] = ""
            break
    return "".join(lines).encode("utf-8")


def fingerprint(
    root: Path,
    rules: tuple[str, ...],
    strip: str | None = None,
    baseline: str | None = None,
) -> str:
    """SHA-256 over the bytes of the files that decide this check.

    Baselines are not in the list: a fingerprint that moved when somebody froze
    one more exception would open a new comparison run for a change that is
    exactly what the page is counting. A path that is not there is part of the
    fingerprint too — a rule file that disappeared is a change of rule.
    """
    digest = hashlib.sha256()
    for pattern in rules:
        wildcard = any(character in pattern for character in "*?[")
        matches = sorted(root.glob(pattern)) if wildcard else [root / pattern]
        if not matches:
            digest.update(f"{pattern}\0missing\0".encode())
            continue
        for path in matches:
            relative = path.relative_to(root).as_posix() if path.is_relative_to(root) else pattern
            digest.update(f"{relative}\0".encode())
            if not path.is_file():
                digest.update(b"missing\0")
                continue
            try:
                data = path.read_bytes()
            except OSError:
                digest.update(b"unreadable\0")
                continue
            text = data.decode("utf-8", errors="replace") if (strip or baseline) else None
            if strip == "ignore_imports" and text is not None:
                data = _strip_ignore_imports(text)
            elif baseline and text is not None:
                data = _strip_baseline_table(text, baseline)
            digest.update(data)
            digest.update(b"\0")
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# What a check reads: the registry above, checked against the checkers
# ---------------------------------------------------------------------------

#: Files a check reads on purpose that must NOT move its fingerprint, with the
#: reason. Two kinds belong here and nothing else does:
#:
#: * a BASELINE — the frozen allowance. Moving one is the debt change the page
#:   counts, so folding it into the fingerprint would open a new comparison run
#:   for exactly the event the page exists to show.
#: * a SUBJECT — a file the check reads in order to judge it. Every edit to it
#:   is the thing being measured, not a new ruler.
#:
#: Anything else a checker reads is part of the rule, and a file missing from
#: its `rules` is a rule change the page would read as nothing but a refactor.
#: The #2205 review found four such files, which is why the registry is now
#: checked against the checkers instead of being trusted.
NOT_A_RULE: dict[str, dict[str, str]] = {
    **{check_id: {
        "frontend/scene-debt-baseline.json": "baseline: exact child, route and network debt sets",
        "frontend/scene-baseline.json": "baseline: the scene rule's frozen ready and debt sets",
    } for check_id in ("scene-non-a-children", "scene-use-route", "legacy-network")},
    "scene-ratchet": {
        "frontend/scene-baseline.json": "baseline: the frozen scene debt",
    },
    "catalog-ratchet": {
        "frontend/catalog-baseline.json": "baseline: the grade-A components not in the catalog yet",
    },
    "fe-boundary": {
        "frontend/import-boundary-baseline.json": "baseline: the frozen boundary violations",
    },
    "be-contracts": {
        "backend/scripts/__init__.py": (
            "not read: the checker asks whether a package marker exists, and the "
            "bare `__init__.py` beside it is not the one it builds the path to"
        ),
    },
    "be-deferred-imports": {
        "backend/deferred-import-baseline.json": (
            "baseline: the frozen per-file count of unannotated function-body imports"
        ),
        "backend/scripts/__init__.py": (
            "not read: the checker asks whether app/__init__.py exists under the "
            "root it is given, and the bare one beside the script is not that file"
        ),
    },
    "stylelint-tokens": {
        "frontend/stylelint-baseline.json": "baseline: the frozen token counts",
    },
    "vue-tsc": {
        "frontend/tsc-baseline.json": "baseline: the frozen per-file error counts",
    },
    "palette": {
        "frontend/palette-baseline.json": "baseline: the frozen colour literals",
        "CLAUDE.md": "subject: the check reads it to judge it, so every edit is the measurement",
        "backend/sandbox/skills/cheese/SKILL.md": (
            "subject: the check reads it to judge it, so every edit is the measurement"
        ),
    },
}

#: A path literal on a line doing one of these is a file the check reads. The
#: filter is what keeps a file merely NAMED in a message ("see this doc") from
#: entering the registry as a rule.
_READ_CONTEXT = (
    "resolve(", "join(", "Path(", "ROOT", "readFileSync", "existsSync", "read_text",
    "open(", "is_file()", "importlib", "spec_from_file_location", "--config", "baseline",
)

#: A quoted string that ends in a file name, even when a variable is in front
#: of it: `resolve(ROOT, 'x.json')` and `"$ROOT/frontend/x.json"` both name a
#: file this check reads, and only the first has a quote before the name.
_PATH_LITERAL = re.compile(r"""['"][^'"]*?([\w][\w./-]*\.(?:py|mjs|cjs|js|json|ini|sh|md))['"]""")
#: Top-level imports only: an import inside a function is still a rule file, and
#: a name in a docstring is not one.
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)
_JS_SPEC = re.compile(r"""(?:from|import)\s*\(?\s*['"](\.[^'"]+)['"]""")
_SH_SOURCE = re.compile(r"^\s*(?:source|\.)\s+['\"]?([^\s'\"]+)", re.M)


def _entry_of(check: Check) -> Path | None:
    """The file a check runs: the one path-shaped argument it is handed."""
    for part in check.argv:
        if part.endswith((".py", ".mjs", ".cjs", ".js", ".sh")):
            return REPO_ROOT / check.cwd / part
    return None


def _rule_paths(root: Path, check: Check) -> set[str]:
    """The registry's list, expanded: a glob in `rules` is as many files."""
    paths: set[str] = set()
    for pattern in check.rules:
        if any(character in pattern for character in "*?["):
            paths.update(path.relative_to(root).as_posix() for path in root.glob(pattern))
        else:
            paths.add(pattern)
    return paths


def _deps_of(path: Path, root: Path, cwd: Path) -> list[Path]:
    """Every repo file `path` reads, as code or as data.

    A checker names a file in two ways, and it uses both: it imports the module
    beside it, and it builds a path and hands it to a reader. The second is the
    one that hides — `resolve(ROOT, 'eslint.boundary.config.mjs')` decides the
    check as much as an `import` does, and nothing in a hand-written registry
    notices it going missing.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    found: list[Path] = []
    if path.suffix == ".py":
        for first, second in _PY_IMPORT.findall(text):
            parts = (first or second).split(".")
            for base in (path.parent, root / ".claude/scripts", root):
                candidate = base.joinpath(*parts)
                hit = next(
                    (c for c in (candidate.with_suffix(".py"), candidate / "__init__.py") if c.is_file()),
                    None,
                )
                if hit is not None:
                    found.append(hit)
                    break
    elif path.suffix in (".mjs", ".js", ".cjs"):
        for spec in _JS_SPEC.findall(text):
            target = (path.parent / spec).resolve()
            hit = next(
                (c for c in (target, *(target.with_suffix(s) for s in (".mjs", ".js", ".cjs"))) if c.is_file()),
                None,
            )
            if hit is not None:
                found.append(hit)
    elif path.suffix == ".sh":
        for target in _SH_SOURCE.findall(text):
            candidate = (path.parent / target).resolve()
            if candidate.is_file():
                found.append(candidate)
    for line in text.splitlines():
        if not any(context in line for context in _READ_CONTEXT):
            continue
        for match in _PATH_LITERAL.finditer(line):
            literal = match.group(1)
            # `"$ROOT/frontend/x.json"` captures `ROOT/frontend/x.json`: the
            # dollar is what says the first segment is a variable, not a
            # directory, so it is dropped before the path is resolved.
            if match.start(1) and line[match.start(1) - 1] == "$":
                literal = literal.split("/", 1)[-1]
            candidate = next(
                (
                    c
                    for c in (root / literal, cwd / literal, (path.parent / literal).resolve())
                    if c.is_file() and c != path
                ),
                None,
            )
            if candidate is not None:
                found.append(candidate)
    return found


def rule_closure(entry: Path, root: Path, cwd: Path) -> set[Path]:
    """Everything `entry` reads, following what those files read in turn.

    `cwd` is the directory the check runs in: a checker builds its paths from
    there and from the repo root, so those are the two anchors a bare filename
    in its source can mean.
    """
    seen: set[Path] = set()
    stack = [entry.resolve()]
    while stack:
        current = stack.pop()
        if current in seen or not current.is_file() or not current.is_relative_to(root):
            continue
        seen.add(current)
        stack.extend(_deps_of(current, root, cwd))
    return seen


# ---------------------------------------------------------------------------
# The snapshot
# ---------------------------------------------------------------------------


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _board(root: Path, argv: tuple[str, ...], out_dir: Path) -> dict | None:
    """arch-metrics.py's own output, or None if the board could not be measured.

    Null rather than an empty object on purpose: the page's size area reads its
    tree-wide numbers from here, and an empty board would draw as a tree with no
    oversized files.
    """
    target = out_dir / "arch-metrics.json"
    command = [_binary(part) for part in argv] + ["--json", str(target)]
    try:
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True, timeout=CHECK_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"ratchet-snapshot: the board could not be measured: {exc}", file=sys.stderr)
        return None
    if result.returncode != 0 or not target.is_file():
        print(
            f"ratchet-snapshot: the board could not be measured (exit "
            f"{result.returncode}): {_tail(result.stderr)}",
            file=sys.stderr,
        )
        return None
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ratchet-snapshot: the board is not readable: {exc}", file=sys.stderr)
        return None


def collect(
    root: Path,
    checks: tuple[Check, ...] = CHECKS,
    board_argv: tuple[str, ...] | None = BOARD_ARGV,
    run_url: str | None = None,
) -> dict:
    """Run every check against `root` and assemble the snapshot."""
    with tempfile.TemporaryDirectory(prefix="ratchet-board-") as raw:
        scratch = Path(raw)
        board = _board(root, board_argv, scratch) if board_argv else None
    records = []
    for check in checks:
        record = run_check(check, root)
        reported = record.pop("id", None)
        # The registry names the check, not the check itself: a checker that
        # renames its own id must not move to another row of the page. A
        # disagreement is kept rather than swallowed, but it does not decide
        # where the numbers go.
        if reported is not None and reported != check.id:
            record["reported_id"] = reported
            print(
                f"ratchet-snapshot: {check.argv[0]} calls itself {reported!r}, "
                f"the registry calls it {check.id!r}",
                file=sys.stderr,
            )
        record["id"] = check.id
        record["area"] = check.area
        record["rule_fingerprint"] = fingerprint(root, check.rules, check.strip, check.baseline)
        records.append(record)
    return {
        "version": SNAPSHOT_VERSION,
        "collection": "ok",
        "commit": _git(root, "rev-parse", "HEAD"),
        "commit_date": _git(root, "show", "-s", "--format=%cI", "HEAD"),
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_url": run_url,
        "checks": records,
        "board": board,
    }


def collect_snapshot(
    root: Path,
    out_path: Path,
    checks: tuple[Check, ...] = CHECKS,
    board_argv: tuple[str, ...] | None = BOARD_ARGV,
    run_url: str | None = None,
) -> dict:
    """Collect, and write down the answer — or why there is none.

    Both answers go to the same file. Everything the checks and the board need
    may be missing on the machine that runs this (a toolchain that never
    installed, a ref that aged out, a temporary directory with no room), and the
    one thing that must not happen is a run that leaves no file at all: that is
    the collector's own version of a zero for a check nobody ran.
    """
    try:
        snapshot = collect(root, checks, board_argv, run_url)
    except Exception as exc:  # noqa: BLE001 — "anything" is the point: nothing may vanish
        snapshot = {
            "version": SNAPSHOT_VERSION,
            "collection": "failed",
            "reason": f"{type(exc).__name__}: {exc}"[:2000],
            "commit": _git(root, "rev-parse", "HEAD"),
            "commit_date": _git(root, "show", "-s", "--format=%cI", "HEAD"),
            "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_url": run_url,
            "checks": [],
            "board": None,
        }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to collect from")
    parser.add_argument("--out", default="ratchet-snapshot.json", help="where to write it")
    parser.add_argument("--run-url", default=None, help="the CI run this came from")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        print(f"cannot judge: {root} is not a git checkout", file=sys.stderr)
        return 2

    out = Path(args.out)
    snapshot = collect_snapshot(root, out, run_url=args.run_url)
    if snapshot["collection"] == "failed":
        print(
            f"collecting failed; {out} says why: {snapshot['reason']}",
            file=sys.stderr,
        )
        return 2

    missing = [check["id"] for check in snapshot["checks"] if check["status"] == "not_collected"]
    failed = [check["id"] for check in snapshot["checks"] if check["status"] == "fail"]
    print(
        f"wrote {out}: {len(snapshot['checks'])} checks at {snapshot['commit']}"
        + (f", not collected: {', '.join(missing)}" if missing else "")
        + (f", failing: {', '.join(failed)}" if failed else ""),
        file=sys.stderr,
    )
    # The snapshot is wanted even when a check is red — it is written after a
    # merge, when "red" has to be recorded rather than acted on.
    return 0


# ---------------------------------------------------------------------------
# Self test. A collector nobody has watched forget a check is a collector that
# quietly stops reporting one.
# ---------------------------------------------------------------------------


_PASS_CHECKER = """\
import json, sys
print(json.dumps({"id": "a", "better": "down", "status": "pass", "actual": 3,
                  "frozen": 4, "stale": [], "details": [{"file": "x", "count": 3}]}))
"""

_FAIL_CHECKER = """\
import json, sys
print(json.dumps({"id": "b", "better": "down", "status": "fail", "actual": 5,
                  "frozen": 4, "stale": [{"file": "x", "frozen": 4, "actual": 5}],
                  "details": []}))
sys.exit(1)
"""

_QUIET_CHECKER = """\
import sys
print("PASS: everything is fine")
"""

_BOOM_CHECKER = """\
import sys
print("cannot judge: no such thing here", file=sys.stderr)
sys.exit(2)
"""

_TWO_LINE_CHECKER = """\
import json
print(json.dumps({"id": "c", "status": "pass"}))
print(json.dumps({"id": "c", "status": "fail"}))
"""

#: Exits 2 (could not judge) while printing a record that claims a pass. The
#: exit code is the authority, so this must never be recorded as a pass with a
#: count of 0 — which is exactly what trusting the record alone would do.
_CONTRADICTS_CHECKER = """\
import json
print(json.dumps({"id": "d", "status": "pass", "actual": 0, "frozen": 0}))
raise SystemExit(2)
"""

_SLOW_CHECKER = """\
import time
time.sleep(30)
"""


def self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: object, want: object) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    with tempfile.TemporaryDirectory(prefix="ratchet-snapshot-selftest-") as raw:
        sandbox = Path(raw)
        root = sandbox / "repo"
        (root / ".claude/scripts").mkdir(parents=True)
        (root / "frontend").mkdir()
        (root / "backend").mkdir()

        for name, source in (
            ("pass.py", _PASS_CHECKER),
            ("fail.py", _FAIL_CHECKER),
            ("quiet.py", _QUIET_CHECKER),
            ("boom.py", _BOOM_CHECKER),
            ("two.py", _TWO_LINE_CHECKER),
            ("contradicts.py", _CONTRADICTS_CHECKER),
            ("slow.py", _SLOW_CHECKER),
        ):
            (root / name).write_text(source)
        (root / "rule.txt").write_text("rule one\n")
        (root / "board.py").write_text(
            "import argparse, json, pathlib\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--quiet', action='store_true')\n"
            "parser.add_argument('--json')\n"
            "args = parser.parse_args()\n"
            "pathlib.Path(args.json).write_text(json.dumps({'meta': {'files': 7}}))\n"
        )

        # A checkout, so the commit fields have something to name.
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        for argv in (
            ["git", "init", "-q"],
            ["git", "add", "-A"],
            ["git", "commit", "-qm", "fixture"],
        ):
            subprocess.run(argv, cwd=root, check=True, capture_output=True, env=env)

        checks = (
            Check(id="passes", area=SCENES, argv=("python", "pass.py"), rules=("rule.txt",)),
            Check(id="fails", area=BOUNDARY, argv=("python", "fail.py"), rules=("rule.txt",)),
            Check(id="quiet", area=SIZE, argv=("python", "quiet.py"), rules=("rule.txt",)),
            Check(id="boom", area=SIZE, argv=("python", "boom.py"), rules=("rule.txt",)),
            Check(id="two-lines", area=TYPES, argv=("python", "two.py"), rules=("rule.txt",)),
            Check(id="contradicts", area=TYPES, argv=("python", "contradicts.py"), rules=("rule.txt",)),
            Check(id="missing", area=TYPES, argv=("python", "not-here.py"), rules=("rule.txt",)),
            Check(id="gone-rule", area=TYPES, argv=("python", "quiet.py"), rules=("no-such-rule",)),
            Check(
                id="slow",
                area=TYPES,
                argv=("python", "slow.py"),
                rules=("rule.txt",),
                timeout=1,
            ),
        )

        snapshot = collect(root, checks, board_argv=("python", "board.py"))

        check("every check is in the snapshot, in order", [c["id"] for c in snapshot["checks"]],
              [c.id for c in checks])
        check("the snapshot names the commit it collected", len(snapshot["commit"] or ""), 40)
        check("... and the board", (snapshot["board"] or {}).get("meta"), {"files": 7})
        check("the version is recorded", snapshot["version"], 1)

        by_id = {c["id"]: c for c in snapshot["checks"]}
        check("a passing check keeps its numbers", by_id["passes"]["actual"], 3)
        check("... and its area", by_id["passes"]["area"], SCENES)
        check("the registry names the check", by_id["passes"]["id"], "passes")
        check("... and the checker's own id is kept beside it",
              by_id["passes"]["reported_id"], "a")
        check("a check that agreed with the registry has no second id",
              "reported_id" in by_id["quiet"], False)
        check("... and its details", by_id["passes"]["details"], [{"file": "x", "count": 3}])
        check("a failing check keeps its numbers too", by_id["fails"]["actual"], 5)
        check("... and stays a failure", by_id["fails"]["status"], "fail")

        # The four ways a check can fail to run, all of them not_collected and
        # none of them a number.
        for name, needle in (
            ("quiet", "no record"),
            ("boom", "no record"),
            ("missing", "not-here.py"),
            ("slow", "still running"),
            ("two-lines", "no record"),
        ):
            check(f"{name} is not collected", by_id[name]["status"], "not_collected")
            check(f"{name} reports no count", by_id[name]["actual"], None)
            check(f"{name} reports no baseline", by_id[name]["frozen"], None)
            check(f"{name} says why", needle in by_id[name]["reason"], True)

        # A checker that exits "could not judge" while its record claims a pass
        # is a contradiction, not a witness. Nothing may record a count of 0
        # here: the exit code is the verdict, and 0 is the one number that reads
        # as "measured, nothing wrong".
        check("an exit code the record disagrees with is not a verdict",
              by_id["contradicts"]["status"], "not_collected")
        check("... and it claims no count", by_id["contradicts"]["actual"], None)
        check("... and it says which two answers it got",
              "disagree" in by_id["contradicts"]["reason"], True)

        # Fingerprints: stable for the same bytes, different for different ones.
        same = fingerprint(root, ("rule.txt",))
        check("a fingerprint is stable", fingerprint(root, ("rule.txt",)), same)
        (root / "rule.txt").write_text("rule two\n")
        check("a changed rule file changes it", fingerprint(root, ("rule.txt",)) != same, True)
        check("a missing rule file is its own fingerprint",
              fingerprint(root, ("no-such-rule",)) == fingerprint(root, ("no-such-rule",)), True)
        check("... and differs from a present one",
              fingerprint(root, ("no-such-rule",)) != fingerprint(root, ("rule.txt",)), True)

        # A ledger check: its verdict is pytest's exit code, and how far the
        # tree is from the ledger is a number only when the test passed. The
        # fixture holds two files and three entries, so a probe that counted
        # files instead of entries would report 2 here — which is the mistake
        # the is_private ledger's BASELINE invites.
        (root / "ledger_probe.py").write_text('_EXEMPT = {"a": 1, "b": 2}\n')
        ledger = Check(
            id="ledger",
            area=BOUNDARY,
            argv=("python", "-m", "pytest", "x.py", "-q"),
            probe=("ledger_probe.py", "sum(_EXEMPT.values())"),
            ledger=True,
        )
        passing = _ledger_record(ledger, 0, "", "", root)
        check("a passing ledger counts its entries, not its files",
              (passing["status"], passing["actual"]), ("pass", 3))
        check("... and its baseline is the same number", passing["frozen"], 3)
        check("the probe is the ledger's own expression",
              _probe(Check(id="l", area=BOUNDARY, argv=(), probe=("ledger_probe.py", "len(_EXEMPT)")), root), 2)
        failing = _ledger_record(ledger, 1, "", "AssertionError: the tree does not match", root)
        check("a failing ledger is a failure", failing["status"], "fail")
        check("... and does not claim to have counted anything", failing["actual"], None)
        check("... while its baseline survives", failing["frozen"], 3)
        for code in (2, 3, 4, 5):
            other = _ledger_record(ledger, code, "", "pytest could not run", root)
            check(f"pytest exit {code} is not a verdict", other["status"], "cannot_judge")
            check(f"... and no count (exit {code})", other["actual"], None)
        unreadable = Check(id="ledger2", area=BOUNDARY, argv=(), probe=("not-here.py", "_EXEMPT"), ledger=True)
        check("a ledger nobody could read has no baseline at all",
              _ledger_record(unreadable, 0, "", "", root)["frozen"], None)
        check("... and reports no count either",
              _ledger_record(unreadable, 0, "", "", root)["actual"], None)
        check("a module this interpreter does not have is not runnable",
              _runnable(root, "no_such_module_anywhere"), False)
        check("... and one it does have is", _runnable(root, "json"), True)

        # The .importlinter exception: freezing one more import is not a change
        # of rule, and must not open a new comparison run.
        base = "root_package = app\n\n[importlinter:contract:c1]\ntype = layers\nlayers =\n    a\n    b\n"
        with_one = base + "ignore_imports =\n    a.x -> b.y\n"
        with_two = base + "ignore_imports =\n    a.x -> b.y\n    a.z -> b.w\n"
        (root / "contracts.ini").write_text(with_one)
        first = fingerprint(root, ("contracts.ini",), "ignore_imports")
        (root / "contracts.ini").write_text(with_two)
        check("freezing another exception is not a rule change", fingerprint(root, ("contracts.ini",), "ignore_imports"), first)
        (root / "contracts.ini").write_text(with_two.replace("type = layers", "type = forbidden"))
        check("changing the contract is", fingerprint(root, ("contracts.ini",), "ignore_imports") != first, True)

        # The pytest ledgers keep their table in the module that is also the
        # rule. #2209 dropped one `_EXEMPT` entry and the fingerprint moved,
        # which would read a debt payment as a new ruler.
        ledger_src = (
            "import pytest\n\n\n"
            "_EXEMPT = frozenset(\n"
            "    (\n        'app.domain.a',\n        'app.domain.b',\n    ),\n"
            "    (\n        'app.domain.c',\n        'app.domain.d',\n    ),\n"
            ")\n\n\n"
            "def test_the_tree_matches():\n    assert _EXEMPT\n"
        )
        (root / "ledger_rule.py").write_text(ledger_src)
        table = fingerprint(root, ("ledger_rule.py",), None, "_EXEMPT")
        (root / "ledger_rule.py").write_text(
            ledger_src.replace("    (\n        'app.domain.c',\n        'app.domain.d',\n    ),\n", "")
        )
        check("paying a ledger entry off is not a rule change",
              fingerprint(root, ("ledger_rule.py",), None, "_EXEMPT"), table)
        (root / "ledger_rule.py").write_text(ledger_src.replace("test_the_tree_matches", "test_the_rule_itself"))
        check("... while changing what the test asserts is",
              fingerprint(root, ("ledger_rule.py",), None, "_EXEMPT") != table, True)
        check("... and the table's extent comes from the parser, not a bracket count",
              _strip_baseline_table("X = 1\n_EXEMPT = frozenset(\n    'a',\n)\nY = 2\n", "_EXEMPT").decode(),
              "X = 1\nY = 2\n")
        check("every ledger names the table that is its baseline",
              [c.id for c in CHECKS if c.ledger and not c.baseline], [])

        # A run that cannot collect still leaves a file. The CI job this feeds
        # could otherwise end with no snapshot AND a green upload step, which
        # reads exactly like a run nobody made — the same mistake as a zero for
        # a check that did not run, one step earlier. (#2205 review)
        real_collect = globals()["collect"]

        def _explode(*args: object, **kwargs: object) -> dict:
            raise RuntimeError("the toolchain was not installed")

        globals()["collect"] = _explode
        try:
            broke = collect_snapshot(root, sandbox / "failed.json", checks=(), board_argv=None)
        finally:
            globals()["collect"] = real_collect
        check("a collection that could not run writes a file anyway",
              (sandbox / "failed.json").is_file(), True)
        check("... marked as failed", broke["collection"], "failed")
        check("... naming what went wrong", "toolchain was not installed" in broke["reason"], True)
        check("... with no checks that could read as passes", broke["checks"], [])
        check("... and no board claiming a tree was measured", broke["board"], None)
        check("... while the same door writes the snapshot when it can",
              collect_snapshot(root, sandbox / "ok.json", checks=(), board_argv=None)["collection"],
              "ok")

        # The registry against the checkers themselves. A rule file left out of
        # `rules` hides a rule change inside a refactor's numbers; a baseline
        # folded INTO the fingerprint opens a new comparison run every time
        # somebody pays a debt. So walk each real checker — its imports and the
        # paths it builds — and make every file it reads answer to one list or
        # the other. The #2205 review found four the registry had missed.
        reads: dict[str, set[str]] = {}
        for registered in CHECKS:
            entry = _entry_of(registered)
            cwd = (REPO_ROOT / registered.cwd).resolve()
            if entry is None or not entry.is_file():
                continue
            reads[registered.id] = {
                path.relative_to(REPO_ROOT).as_posix() for path in rule_closure(entry, REPO_ROOT, cwd)
            }
        check("every check runs a file this repo has",
              sorted(reads), sorted(c.id for c in CHECKS))
        for registered in CHECKS:
            excused = NOT_A_RULE.get(registered.id, {})
            known = _rule_paths(REPO_ROOT, registered) | set(excused)
            check(
                f"{registered.id} accounts for every file it reads",
                sorted(rel for rel in reads.get(registered.id, set()) if rel not in known),
                [],
            )
            for rel, why in excused.items():
                check(f"{registered.id}: {rel} is excused, so it exists", (REPO_ROOT / rel).is_file(), True)
                check(f"{registered.id}: {rel} is excused, so it is not a rule too",
                      rel in _rule_paths(REPO_ROOT, registered), False)
                check(f"{registered.id}: {rel} says why in a line", len(why) > 20, True)
        check("no check excuses a file it never reads",
              sorted(f"{cid}/{rel}" for cid, excused in NOT_A_RULE.items()
                     for rel in excused if rel not in reads.get(cid, set())),
              [])

        # The other half of the same contract: a file ON the list has to move
        # the fingerprint when its bytes change. A rule that can be edited with
        # the page none the wiser is the same defect one step later.
        for registered in CHECKS:
            for rel in sorted(_rule_paths(REPO_ROOT, registered)):
                source = REPO_ROOT / rel
                if not source.is_file():
                    check(f"{registered.id}: rule file {rel} is in the repo", False, True)
                    continue
                mirror = sandbox / "mirror" / registered.id
                target = mirror / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read_bytes())
                before = fingerprint(mirror, (rel,), registered.strip, registered.baseline)
                target.write_bytes(source.read_bytes() + b"\n# a rule change\n")
                check(
                    f"{registered.id}: editing {rel} moves the fingerprint",
                    fingerprint(mirror, (rel,), registered.strip, registered.baseline) != before,
                    True,
                )

        # The registry itself: an empty or duplicated list is how a check
        # silently stops being reported.
        ids = [c.id for c in CHECKS]
        check("the registry has no duplicates", len(ids), len(set(ids)))
        check("every registered check has rules to fingerprint",
              [c.id for c in CHECKS if not c.rules], [])
        check("every area is one of the four", sorted({c.area for c in CHECKS}),
              sorted({SCENES, BOUNDARY, SIZE, TYPES}))

    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print(f"  {line}")
        return 1
    print(
        "PASS: ratchet-snapshot self-test (a check that did not run is not_collected "
        "with no count, every check is kept in order, the board is stored as written, "
        "and fingerprints move with the rules and not with the baseline)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
