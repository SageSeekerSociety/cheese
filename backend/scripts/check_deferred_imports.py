#!/usr/bin/env python3
"""Gate: imports inside a function body under `backend/app` may only go down.

    cd backend && uv run python scripts/check_deferred_imports.py
    cd backend && uv run python scripts/check_deferred_imports.py --self-test
    cd backend && uv run python scripts/check_deferred_imports.py --update
    cd backend && uv run python scripts/check_deferred_imports.py --update --freeze-new

    0 = no file has more unexplained deferred imports than its baseline allows
    1 = a file grew one (or `--update` was asked to freeze growth)
    2 = could not judge (no app/, no or malformed baseline, a file that does
        not parse) — never a pass

THE RULE. An `import` / `from ... import` statement inside a function body is
legal when it says why, in a comment on the same line or on the line directly
above it:

    # deferred-import: breaks the cycle domain.topic -> domain.project
    from app.domain.project.services import ProjectService

    import pygit2  # deferred-import: optional dependency, absent on the CLI image

Typical reasons: breaking an import cycle, an optional dependency, start-up
cost, a test double that patches the attribute on its home module. Annotated
imports are not limited; every run lists them so a reviewer can see them.

Unannotated ones are frozen per file in `deferred-import-baseline.json`: a file
that is not in it must have none, and a file that is may only go down.

WHY. A function-body import is invisible to anyone reading the top of the file
and to every tool that reads the import graph from module level; import-linter
sees it, almost nothing else does. It is also the usual way a cycle is dodged
rather than removed (27 of the 29 frozen layer violations in `.importlinter`
are function-body imports). Either lift it to the top of the module, or write
down why it cannot be.

COUNTING. Same as `.claude/scripts/arch-metrics.py`'s `deferred_imports`, which
imports `count_deferred_imports` from this file rather than keeping a copy: by
`ast`, an import lexically inside a `def`/`async def` at any depth, each
statement once (an import inside a nested function is one import, not one per
enclosing function). A module-level `if TYPE_CHECKING:` import is not inside a
function and is not counted; neither is one in a class body.

`--update` IS SHRINK-ONLY. It lowers every entry to what the tree has and drops
files that reached zero. If any file has grown it writes nothing, prints the
growth and exits 1; `--freeze-new` writes it anyway and prints every line it
froze, so the commit's diff says what was accepted.

This file must stay standard-library only: arch-metrics loads it with a bare
`python3`, outside the backend's virtualenv.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve()
BACKEND_ROOT = HERE.parents[1]
BASELINE_NAME = "deferred-import-baseline.json"

OK, BROKEN, CANNOT_JUDGE = 0, 1, 2

#: `# deferred-import: <reason>`; an empty reason is not a reason.
MARKER_RE = re.compile(r"#\s*deferred-import:\s*(?P<reason>\S.*?)\s*$")

BASELINE_COMMENT = (
    "Unannotated imports inside a function body under backend/app, per file. "
    "May only go down: a new file must have none. Annotate one with "
    "`# deferred-import: <reason>` (same line or the line above) or lift it to "
    "module level. Refresh with `uv run python scripts/check_deferred_imports.py "
    "--update` (shrink-only). The rule is in .claude/rules/architecture.md."
)

UPDATE_HINT = (
    "lift the import to module level, or annotate it with "
    "`# deferred-import: <reason>`; to freeze it on purpose: "
    "uv run python scripts/check_deferred_imports.py --update --freeze-new"
)


# ---------------------------------------------------------------------------
# Counting. Shared with .claude/scripts/arch-metrics.py — keep it stdlib-only.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeferredImport:
    line: int
    statement: str
    #: The text after `# deferred-import:`, or None when unannotated.
    reason: str | None


@dataclass
class Scan:
    #: path relative to the scanned root's parent (e.g. "app/core/x.py")
    imports: dict[str, list[DeferredImport]] = field(default_factory=dict)
    unparseable: list[str] = field(default_factory=list)

    def unannotated(self) -> dict[str, int]:
        counts = {
            path: sum(1 for item in items if item.reason is None)
            for path, items in self.imports.items()
        }
        return {path: n for path, n in sorted(counts.items()) if n}

    def annotated(self) -> list[tuple[str, DeferredImport]]:
        return [
            (path, item)
            for path, items in sorted(self.imports.items())
            for item in items
            if item.reason is not None
        ]

    def total(self) -> int:
        return sum(len(items) for items in self.imports.values())


def _reason(lines: list[str], node: ast.stmt) -> str | None:
    """The annotation on any line of the statement, or on the line above it."""
    end = getattr(node, "end_lineno", None) or node.lineno
    for number in range(node.lineno, end + 1):
        match = MARKER_RE.search(lines[number - 1])
        if match:
            return match.group("reason")
    if node.lineno >= 2:
        above = lines[node.lineno - 2].strip()
        if above.startswith("#"):
            match = MARKER_RE.search(above)
            if match:
                return match.group("reason")
    return None


def deferred_imports_in(source: str) -> list[DeferredImport]:
    """Every import statement lexically inside a function body, once each.

    Raises SyntaxError / ValueError for a source that does not parse.
    """
    tree = ast.parse(source)
    lines = source.splitlines()
    found: list[DeferredImport] = []

    def walk(node: ast.AST, in_function: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if in_function and isinstance(child, ast.Import | ast.ImportFrom):
                first = lines[child.lineno - 1].strip()
                found.append(DeferredImport(child.lineno, first, _reason(lines, child)))
            walk(
                child,
                in_function
                or isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef),
            )

    walk(tree, False)
    return found


def scan(app_dir: Path) -> Scan:
    """Deferred imports in every `*.py` under `app_dir`, keyed by path relative
    to `app_dir.parent` (so `app/...`, the way the baseline names them)."""
    result = Scan()
    for path in sorted(app_dir.rglob("*.py")):
        if not path.is_file():
            continue
        relative = path.relative_to(app_dir.parent).as_posix()
        try:
            found = deferred_imports_in(
                path.read_text(encoding="utf-8", errors="replace")
            )
        except (SyntaxError, ValueError):
            result.unparseable.append(relative)
            continue
        if found:
            result.imports[relative] = found
    return result


def count_deferred_imports(app_dir: Path) -> int:
    """All deferred imports under `app_dir`, annotated or not — the board's number.

    A file that does not parse is skipped: the board does not invent a number
    for it (the gate, which needs every file, refuses to judge instead).
    """
    return scan(app_dir).total()


# ---------------------------------------------------------------------------
# The baseline.
# ---------------------------------------------------------------------------


class BaselineError(Exception):
    """The baseline cannot be read as one this script wrote."""


def read_baseline(path: Path) -> dict[str, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BaselineError(f"no baseline at {path}") from exc
    except (OSError, ValueError) as exc:
        raise BaselineError(f"{path} is not readable JSON: {exc}") from exc
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        raise BaselineError(f'{path} has no "files" object')
    for name, count in files.items():
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            raise BaselineError(
                f"{path}: {name!r} is frozen at {count!r}; an entry is a positive "
                "integer (a file at zero is dropped, not kept at 0)"
            )
    return dict(files)


def render_baseline(files: dict[str, int]) -> str:
    body = {"_comment": BASELINE_COMMENT, "files": dict(sorted(files.items()))}
    return json.dumps(body, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# The verdict.
# ---------------------------------------------------------------------------


def _judge(root: Path, baseline_path: Path) -> tuple[Scan, dict[str, int]]:
    """(scan, frozen), or exit 2 with the reason."""
    app_dir = root / "app"
    if not (app_dir / "__init__.py").is_file():
        print(f"cannot judge: no app/ package under {root}", file=sys.stderr)
        sys.exit(CANNOT_JUDGE)
    result = scan(app_dir)
    if result.unparseable:
        print(
            "cannot judge: these files do not parse, so their imports cannot be "
            "counted:\n" + "\n".join(f"  {p}" for p in result.unparseable),
            file=sys.stderr,
        )
        sys.exit(CANNOT_JUDGE)
    try:
        frozen = read_baseline(baseline_path)
    except BaselineError as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        sys.exit(CANNOT_JUDGE)
    return result, frozen


def _print_annotated(result: Scan) -> None:
    annotated = result.annotated()
    print(
        f"annotated deferred imports (not limited, listed for review): {len(annotated)}"
    )
    for path, item in annotated:
        print(f"  {path}:{item.line}  {item.reason}")


def check(root: Path, baseline_path: Path) -> int:
    result, frozen = _judge(root, baseline_path)
    actual = result.unannotated()

    grown = {
        path: (frozen.get(path, 0), count)
        for path, count in actual.items()
        if count > frozen.get(path, 0)
    }
    stale = {
        path: (count, actual.get(path, 0))
        for path, count in frozen.items()
        if actual.get(path, 0) < count
    }

    _print_annotated(result)
    if stale:
        print(
            f"\nwarning: {len(stale)} baseline entr{'y' if len(stale) == 1 else 'ies'} "
            "above the tree (debt paid down; lower it with --update):"
        )
        for path, (was, now) in sorted(stale.items()):
            print(f"  {path}: frozen {was}, now {now}")

    if grown:
        print(
            f"\nFAIL: {len(grown)} file(s) have more unannotated deferred imports "
            f"than {baseline_path.name} allows:"
        )
        for path, (allowed, count) in sorted(grown.items()):
            print(f"  {path}: {count} (allowed {allowed})")
            for item in result.imports[path]:
                if item.reason is None:
                    print(f"    line {item.line}: {item.statement}")
        print(f"\n{UPDATE_HINT}")
        return BROKEN

    print(
        f"\nPASS: deferred imports held ({sum(actual.values())} unannotated in "
        f"{len(actual)} files, baseline {sum(frozen.values())}; "
        f"{len(result.annotated())} annotated; {result.total()} in all)"
    )
    return OK


def update(root: Path, baseline_path: Path, freeze_new: bool) -> int:
    if not baseline_path.exists() and freeze_new:
        baseline_path.write_text(render_baseline({}), encoding="utf-8")
        print(f"created an empty {baseline_path.name} to freeze into")
    result, frozen = _judge(root, baseline_path)
    actual = result.unannotated()

    added = {
        path: (frozen.get(path, 0), count)
        for path, count in actual.items()
        if count > frozen.get(path, 0)
    }
    lowered = {
        path: (count, actual.get(path, 0))
        for path, count in frozen.items()
        if actual.get(path, 0) < count
    }

    if added and not freeze_new:
        print(
            f"{len(added)} file(s) grew; nothing written. These are what the check "
            "exists to catch:",
            file=sys.stderr,
        )
        for path, (was, now) in sorted(added.items()):
            print(f"  {path}: {was} -> {now}", file=sys.stderr)
        print(UPDATE_HINT, file=sys.stderr)
        return BROKEN

    for path, (was, now) in sorted(lowered.items()):
        print(f"  - {path}: {was} -> {now}")
    if added:
        print(
            f"freezing {sum(n - w for w, n in added.values())} new deferred import(s):"
        )
        for path, (was, now) in sorted(added.items()):
            print(f"  + {path}: {was} -> {now}")
            for item in result.imports[path]:
                if item.reason is None:
                    print(f"      line {item.line}: {item.statement}")

    if not added and not lowered:
        print(f"unchanged: {baseline_path.name} matches the tree")
        return OK

    baseline_path.write_text(render_baseline(actual), encoding="utf-8")
    # A refresh that leaves the check red looks like it worked; verify it.
    verdict = subprocess.run(
        [
            sys.executable,
            str(HERE),
            "--root",
            str(root),
            "--baseline",
            str(baseline_path),
        ],
        capture_output=True,
        text=True,
    )
    if verdict.returncode != OK:
        print(
            f"cannot judge: the rewritten baseline does not pass (exit "
            f"{verdict.returncode})\n{verdict.stdout}{verdict.stderr}",
            file=sys.stderr,
        )
        return CANNOT_JUDGE
    print(
        f"wrote {baseline_path.name}: {sum(actual.values())} unannotated deferred "
        f"imports in {len(actual)} files"
    )
    return OK


# ---------------------------------------------------------------------------
# --self-test: build small trees, require the check to go red and green.
# ---------------------------------------------------------------------------

_NESTED = (
    "def outer():\n"
    "    def inner():\n"
    "        import json\n"
    "\n"
    "        return json\n"
    "\n"
    "    return inner\n"
)
_TYPE_CHECKING = (
    "from typing import TYPE_CHECKING\n"
    "\n"
    "if TYPE_CHECKING:\n"
    "    from app.other import Thing\n"
    "\n"
    "\n"
    "class Holder:\n"
    "    import os\n"
)
_ANNOTATED = (
    "def same_line():\n"
    "    import json  # deferred-import: test double patches json.dumps\n"
    "    return json\n"
    "\n"
    "\n"
    "def line_above():\n"
    "    # deferred-import: breaks the cycle app.a -> app.b\n"
    "    from app.b import (\n"
    "        thing,\n"
    "    )\n"
    "    return thing\n"
)
_EMPTY_REASON = "def f():\n    import json  # deferred-import:\n    return json\n"
_TWO = "def f():\n    import json\n    import re\n    return json, re\n"


def _plant(
    tmp: Path, files: dict[str, str], baseline: dict[str, int] | str | None
) -> Path:
    root = tmp / f"tree{len(list(tmp.iterdir()))}"
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text("")
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    if isinstance(baseline, dict):
        (root / BASELINE_NAME).write_text(render_baseline(baseline))
    elif isinstance(baseline, str):
        (root / BASELINE_NAME).write_text(baseline)
    return root


def _invoke(root: Path, *extra: str) -> tuple[int, str]:
    run = subprocess.run(
        [sys.executable, str(HERE), "--root", str(root), *extra],
        capture_output=True,
        text=True,
    )
    return run.returncode, run.stdout + run.stderr


def self_test() -> int:
    failures: list[str] = []

    def expect(
        label: str, got: tuple[int, str], code: int, needle: str | None = None
    ) -> None:
        status, output = got
        ok = status == code and (needle is None or needle in output)
        print(f"  [{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            tail = "\n".join(output.strip().splitlines()[-12:])
            failures.append(
                f"{label}: exit {status} (want {code}"
                + (f", output containing {needle!r}" if needle else "")
                + f")\n{tail}"
            )

    with tempfile.TemporaryDirectory(prefix="deferred-imports-selftest-") as raw:
        tmp = Path(raw)

        # The counting itself, in-process: the numbers the cases below rely on.
        counted = {
            "nested": len(deferred_imports_in(_NESTED)),
            "type_checking": len(deferred_imports_in(_TYPE_CHECKING)),
            "annotated": [i.reason is None for i in deferred_imports_in(_ANNOTATED)],
            "empty_reason": [i.reason for i in deferred_imports_in(_EMPTY_REASON)],
        }
        want = {
            "nested": 1,
            "type_checking": 0,
            "annotated": [False, False],
            "empty_reason": [None],
        }
        for key, value in want.items():
            ok = counted[key] == value
            print(f"  [{'ok' if ok else 'FAIL'}] counts {key}: {counted[key]!r}")
            if not ok:
                failures.append(f"counts {key}: got {counted[key]!r}, want {value!r}")

        expect("a clean tree passes", _invoke(_plant(tmp, {}, {})), OK, "PASS")
        expect(
            "a new file with a deferred import fails",
            _invoke(_plant(tmp, {"app/new.py": _TWO}, {})),
            BROKEN,
            "app/new.py: 2 (allowed 0)",
        )
        expect(
            "a nested function's import counts once (frozen at 1 passes)",
            _invoke(_plant(tmp, {"app/n.py": _NESTED}, {"app/n.py": 1})),
            OK,
        )
        expect(
            "a nested function's import counts (frozen at 0 fails)",
            _invoke(_plant(tmp, {"app/n.py": _NESTED}, {})),
            BROKEN,
            "app/n.py: 1 (allowed 0)",
        )
        expect(
            "module-level TYPE_CHECKING and class-body imports are not counted",
            _invoke(_plant(tmp, {"app/t.py": _TYPE_CHECKING}, {})),
            OK,
        )
        expect(
            "annotated imports pass and are listed",
            _invoke(_plant(tmp, {"app/a.py": _ANNOTATED}, {})),
            OK,
            "app/a.py:8  breaks the cycle app.a -> app.b",
        )
        expect(
            "an annotation with no reason is not one",
            _invoke(_plant(tmp, {"app/e.py": _EMPTY_REASON}, {})),
            BROKEN,
        )
        expect(
            "a frozen file may not grow",
            _invoke(_plant(tmp, {"app/g.py": _TWO}, {"app/g.py": 1})),
            BROKEN,
            "app/g.py: 2 (allowed 1)",
        )
        expect(
            "a baseline above the tree only warns",
            _invoke(
                _plant(tmp, {"app/s.py": _NESTED}, {"app/s.py": 3, "app/gone.py": 1})
            ),
            OK,
            "app/gone.py: frozen 1, now 0",
        )
        expect(
            "no baseline cannot be judged",
            _invoke(_plant(tmp, {}, None)),
            CANNOT_JUDGE,
            "no baseline",
        )
        expect(
            "a malformed baseline cannot be judged",
            _invoke(_plant(tmp, {}, '{"files": {"app/x.py": 0}}')),
            CANNOT_JUDGE,
        )
        expect(
            "a file that does not parse cannot be judged",
            _invoke(_plant(tmp, {"app/bad.py": "def f(:\n"}, {})),
            CANNOT_JUDGE,
            "app/bad.py",
        )

        # --update: lowers, refuses growth, freezes on purpose.
        root = _plant(tmp, {"app/s.py": _NESTED}, {"app/s.py": 3, "app/gone.py": 1})
        expect("--update lowers a paid-down baseline", _invoke(root, "--update"), OK)
        written = read_baseline(root / BASELINE_NAME)
        ok = written == {"app/s.py": 1}
        print(f"  [{'ok' if ok else 'FAIL'}] --update wrote {written!r}")
        if not ok:
            failures.append(f"--update wrote {written!r}, want {{'app/s.py': 1}}")

        root = _plant(tmp, {"app/g.py": _TWO, "app/s.py": _NESTED}, {"app/s.py": 2})
        before = (root / BASELINE_NAME).read_text()
        expect("--update refuses growth", _invoke(root, "--update"), BROKEN, "grew")
        ok = (root / BASELINE_NAME).read_text() == before
        print(f"  [{'ok' if ok else 'FAIL'}] --update refusing growth writes nothing")
        if not ok:
            failures.append("--update refusing growth still rewrote the baseline")
        expect(
            "--update --freeze-new freezes growth and says what",
            _invoke(root, "--update", "--freeze-new"),
            OK,
            "+ app/g.py: 0 -> 2",
        )
        expect("…after which the tree passes", _invoke(root), OK, "PASS")

    if failures:
        print(f"\nFAIL: check_deferred_imports self-test ({len(failures)} case(s))")
        for failure in failures:
            print(f"- {failure}")
        return BROKEN
    print(
        "PASS: check_deferred_imports self-test (a new or grown deferred import "
        "fires, nested functions count once, TYPE_CHECKING and annotated imports "
        "do not count, a stale baseline only warns, --update only shrinks)"
    )
    return OK


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--root",
        default=str(BACKEND_ROOT),
        help="the directory holding app/ (default: backend/)",
    )
    parser.add_argument(
        "--baseline", default=None, help=f"default: <root>/{BASELINE_NAME}"
    )
    parser.add_argument(
        "--update", action="store_true", help="lower the baseline to the tree"
    )
    parser.add_argument(
        "--freeze-new",
        action="store_true",
        help="with --update: also freeze growth, printing it",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    root = Path(args.root).resolve()
    baseline = Path(args.baseline) if args.baseline else root / BASELINE_NAME
    if args.freeze_new and not args.update:
        print("cannot judge: --freeze-new only means something with --update")
        return CANNOT_JUDGE
    if args.update:
        return update(root, baseline, args.freeze_new)
    return check(root, baseline)


if __name__ == "__main__":
    sys.exit(main())
