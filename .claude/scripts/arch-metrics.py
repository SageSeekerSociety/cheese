#!/usr/bin/env python3
"""arch-metrics.py — the architecture board: every number P3 watches, in one run.

    python3 .claude/scripts/arch-metrics.py                    write ./arch-metrics.{json,md}
    python3 .claude/scripts/arch-metrics.py --compare 721f7b04 another revision, with the deltas
    python3 .claude/scripts/arch-metrics.py --self-test        prove the numbers are counted

WHY A BOARD AND NOT A GATE. The three checks in `.claude/rules/architecture.md`
block a new violation. None of them can say whether the tree is getting better,
because each is a ratchet against a frozen baseline and a ratchet that holds
still reports success forever. This script is the other half: it reads the same
declarations the checks read and prints where they stand, so "did the last month
help" is a diff of two runs instead of an argument. It deliberately exits 0 on a
bad number — a board that goes red is a gate, and every gate here is one
somebody has to fund. `--compare` is how it earns its keep: the same numbers at
another revision, side by side.

WHAT IT MEASURES, and how each number is obtained:

  frontend.components      `.vue` files under `frontend/src`. A component is a
                           file with a template.
  frontend.grades          Each component graded A/B/C/D with the method from
                           the frontend audit (see "the grade" below).
  frontend.boundary        Violations frozen in `frontend/import-boundary-baseline.json`
                           — read, not re-derived: the ESLint rule that produces
                           them needs node_modules, and this has to run anywhere.
  backend.contracts        Frozen `importer -> imported` pairs per contract in
                           `backend/.importlinter`, C1 + C2 + C3 (counts in
                           `.claude/rules/architecture.md`).
  backend.deferred_imports `Import`/`ImportFrom` statements lexically inside a
                           function body under `backend/app`, via `ast`. This is
                           the shape a cycle is dodged with, which is why the
                           contracts count them too.
  backend.files_over_1000  `backend/app/**/*.py` over 1000 lines; the second
                           number is the count over 1500, the size cap.
  backend.chat_py_lines    `backend/app/domain/agent/chat.py` — the P2 pilot, on
                           the board so the pilot has a line to move.
  size.offenders           Files over the cap `.claude/scripts/check-file-sizes.py`
                           declares, and how many lines they are over in total.
                           The caps are imported from that script, not restated,
                           so the board and the gate cannot disagree.
  hotspots                 Last 30 and 90 days of git history: the top 10 files
                           by changes x current line count, each with the share
                           of those commits whose subject starts `fix(`/`fix:`/
                           `revert`. Changes x lines is the honest proxy for how
                           much of the tree a file holds hostage: a file that is
                           both large and edited often is where a boundary
                           mistake is expensive.
  hotspots.shared_days     (file, day) pairs with two or more DISTINCT commits in
                           the 90-day window. One file touched twice in a day is
                           how parallel work collides; one commit touching a file
                           twice is impossible.

  Scope of the git metrics: `backend/app/**/*.py` and `frontend/src/**/*.{vue,ts,js}`,
  minus `*.spec.ts` and `.../test/...` — the same trees the size caps judge, minus
  the tests, which churn with their subject rather than on their own. Merges are
  skipped (`--no-merges`): this repository squash-merges, and a merge commit
  carries no diff of its own.

THE GRADE, ported from the frontend audit's `analyze.py` (B-data) and simplified:

  D  anything that ties the component to where it is mounted or to a channel
     other than props/emits: `useRoute`/`useRouter`/`$router`/`vue-router`,
     `$parent`/`$root`, an event bus, or `provide`/`inject`.
  C  it reaches the network — it imports `@/api`, `@/services/*`, `@/network/*`,
     OR any module that transitively reaches one of those, OR uses `fetch`/
     `axios` itself — or it reads a business store.
  B  it reads only app-chrome stores (`usePageTitleStore`, `useNavigationStore`).
  A  none of the above: it can be rendered from props and emits alone. This is
     the grade `.claude/scripts/scene-ratchet.py` calls standalone-ready and
     fails a new scene on, so the ratchet is a second reader of this number
     rather than a second opinion about it.

  The method itself — every regex, every simplification and what each costs —
  lives in `.claude/scripts/frontend_grade.py`, which this board and the scene
  ratchet both load. A copy here would be a second answer to "can this run
  alone", and the two would eventually disagree about a component that one of
  them fails.

Exit codes: 0 the board was produced, 1 the self-test failed, 2 could not judge
(git missing, `--compare` given a ref this checkout does not have). A number
that is missing because its input is missing — `.importlinter` before it landed,
say — is reported as null, never as zero.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The two trees the size caps judge — the scope of the file and git metrics.
HOT_PREFIXES = ("backend/app/", "frontend/src/")
HOT_SUFFIXES = (".py", ".vue", ".ts", ".js")
HOT_SKIP_SUFFIXES = (".spec.ts",)

#: The windows the hotspots are read over.
WINDOWS = (30, 90)

#: A commit that is fixing something, by Conventional Commits subject.
FIX_SUBJECT = re.compile(r"^(?:fix|revert)(?:\(|!|:)", re.I)

# ---------------------------------------------------------------- git plumbing


def git(root: Path, *args: str) -> str:
    """Run git in `root` and return stdout. Raises CalledProcessError."""
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout


def git_meta(root: Path) -> dict[str, Any]:
    """The commit this board describes."""
    try:
        head = git(root, "log", "-1", "--format=%H|%cI|%s").strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {"commit": None, "date": None, "subject": None}
    sha, date, subject = head.split("|", 2)
    return {"commit": sha[:12], "date": date, "subject": subject}


# ---------------------------------------------------------------- the frontend

def load_frontend_grade() -> Any:
    """The grader, imported from the module this board and the gate both read.

    Loaded from THIS script's directory, never from a `--root` tree: the rules
    a board measures against are the repository's, and a tree to measure is
    only a tree to measure. `frontend_grade.py` is the single definition of
    what "standalone-ready" means — `scene-ratchet.py` fails a scene on the
    same function this reports, which is the one thing the two must not
    disagree about. (The caps above are loaded the same way, from the gate
    that enforces them.)
    """
    source = Path(__file__).resolve().parent / "frontend_grade.py"
    spec = importlib.util.spec_from_file_location("_frontend_grade", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable script
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_frontend_grade"] = module
    spec.loader.exec_module(module)
    return module


def grade_frontend(root: Path) -> dict[str, Any]:
    """Count `.vue` components and grade each one A/B/C/D.

    What each letter means, how it is read out of a file, and what a regex
    over a template cannot see live in `.claude/scripts/frontend_grade.py`.
    """
    return load_frontend_grade().grade_frontend(root)


def frontend_boundary(root: Path) -> dict[str, Any]:
    """The frozen component-boundary violations, read from the ratchet's file."""
    path = root / "frontend" / "import-boundary-baseline.json"
    if not path.is_file():
        return {"files": None, "violations": None, "why_missing": "no baseline file"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"files": None, "violations": None, "why_missing": f"unreadable: {exc}"}
    frozen = data.get("files") or {}
    return {"files": len(frozen), "violations": sum(frozen.values())}


# ---------------------------------------------------------------- the backend


def parse_importlinter(path: Path) -> dict[str, Any] | None:
    """Frozen pair counts per contract in `.importlinter`.

    Not configparser: the baseline is a multi-line value whose entries look like
    `importer -> imported`, and INI's continuation rules would fold the comments
    between contracts into whichever value they follow. One pass, by section.
    """
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    contracts: dict[str, int] = {}
    section: str | None = None
    collecting = False
    for raw in text.splitlines():
        line = raw.rstrip()
        header = re.match(r"^\[importlinter:contract:([^\]]+)\]$", line)
        if header:
            section, collecting = header.group(1), False
            contracts.setdefault(section, 0)
            continue
        if not section or not line.strip() or line.lstrip().startswith("#"):
            continue
        key = re.match(r"^([a-z_]+)\s*=\s*(.*)$", line)
        if key:
            collecting = key.group(1) == "ignore_imports"
            if collecting and key.group(2).strip():
                contracts[section] += 1
            continue
        if collecting:  # an indented continuation line
            contracts[section] += 1
    return {
        "per_contract": contracts,
        "total": sum(contracts.values()),
    }


def load_deferred_imports() -> Any:
    """The counter, imported from the gate that enforces it.

    Loaded from this repository's `backend/scripts/check_deferred_imports.py`,
    never from a `--root` tree, for the same reason as the caps below: the
    board and the gate must not have two definitions of "an import inside a
    function" to drift apart.
    """
    source = REPO_ROOT / "backend" / "scripts" / "check_deferred_imports.py"
    spec = importlib.util.spec_from_file_location("_check_deferred_imports", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable script
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_check_deferred_imports"] = module
    spec.loader.exec_module(module)
    return module


def count_deferred_imports(root: Path) -> int | None:
    """Import statements inside a function body under `backend/app`, annotated
    with `# deferred-import:` or not.

    AST, so a statement inside `if TYPE_CHECKING:` at module level is not one of
    these and an import inside a nested function is counted once. The counting
    is `check_deferred_imports.py`'s; a file that does not parse is skipped.
    """
    app = root / "backend" / "app"
    if not app.is_dir():
        return None
    return load_deferred_imports().count_deferred_imports(app)


def count_lines(data: bytes) -> int:
    """Lines as an editor counts them (the same rule check-file-sizes uses)."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def file_lines(path: Path) -> int | None:
    try:
        return count_lines(path.read_bytes())
    except OSError:
        return None


def backend_files(root: Path) -> dict[str, Any]:
    app = root / "backend" / "app"
    over_1000 = over_1500 = files = 0
    if app.is_dir():
        for path in sorted(app.rglob("*.py")):
            if not path.is_file():
                continue
            lines = file_lines(path)
            if lines is None:
                continue
            files += 1
            if lines > 1000:
                over_1000 += 1
            if lines > 1500:
                over_1500 += 1
    chat = root / "backend" / "app" / "domain" / "agent" / "chat.py"
    return {
        "python_files": files,
        "files_over_1000": over_1000,
        "files_over_1500": over_1500,
        "chat_py_lines": file_lines(chat) if chat.is_file() else None,
    }


# ---------------------------------------------------------------- file sizes


def load_size_caps(root: Path) -> Any:
    """The cap declarations, imported from the gate that enforces them.

    Loaded from THIS script's directory, never from `root`: the rules a board
    measures against are the repository's, and a `--root` tree is only a tree to
    measure. A copy of the rules here would eventually disagree with the gate.
    """
    source = Path(__file__).resolve().parent / "check-file-sizes.py"
    spec = importlib.util.spec_from_file_location("_check_file_sizes", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable script
        raise ImportError(f"cannot load {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_check_file_sizes"] = module
    spec.loader.exec_module(module)
    return module


def size_report(root: Path, caps: Any) -> dict[str, Any]:
    """Files over their cap, and how far over, per cap."""
    per_cap: dict[str, dict[str, int]] = {}
    offenders: list[dict[str, Any]] = []
    for cap in caps.CAPS:
        over = excess = judged = 0
        for path in sorted((root / cap.prefix).rglob("*")):
            if not path.is_file() or not path.name.endswith(cap.extensions):
                continue
            rel = path.relative_to(root).as_posix()
            if rel in caps.EXEMPT:
                continue
            lines = file_lines(path)
            if lines is None:
                continue
            judged += 1
            if lines > cap.limit:
                over += 1
                excess += lines - cap.limit
                offenders.append(
                    {"file": rel, "lines": lines, "cap": cap.limit, "over": lines - cap.limit}
                )
        per_cap[cap.prefix] = {
            "judged": judged,
            "over_cap": over,
            "excess_lines": excess,
            "cap": cap.limit,
        }
    offenders.sort(key=lambda o: -o["over"])
    return {
        "per_cap": per_cap,
        "offenders": len(offenders),
        "excess_lines": sum(o["over"] for o in offenders),
        "worst": offenders[:10],
    }


# ---------------------------------------------------------------- hotspots


def read_commits(root: Path, days: int) -> list[dict[str, Any]]:
    """Commits in the last `days` days, with the files each one touched.

    `--no-merges`: this repository squash-merges, so a merge commit's own diff is
    empty and its subject is not a Conventional Commit. `--name-only` prints no
    file list for a merge anyway, and reporting one as a change to nothing would
    only dilute the ratios.
    """
    raw = git(
        root, "log", "--no-merges", f"--since={days} days ago", "--name-only", "--format=@@%H|%cI|%s"
    )
    commits: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in raw.splitlines():
        if line.startswith("@@"):
            sha, date, subject = line[2:].split("|", 2)
            current = {"sha": sha, "day": date[:10], "subject": subject, "files": []}
            commits.append(current)
        elif line.strip() and current is not None:
            current["files"].append(line.strip())
    return commits


def in_scope(path: str) -> bool:
    return (
        path.startswith(HOT_PREFIXES)
        and path.endswith(HOT_SUFFIXES)
        and not path.endswith(HOT_SKIP_SUFFIXES)
        and "/test/" not in path
    )


def hotspots(root: Path, days: int) -> dict[str, Any]:
    commits = read_commits(root, days)
    changes: Counter[str] = Counter()
    fixes: Counter[str] = Counter()
    for commit in commits:
        is_fix = bool(FIX_SUBJECT.match(commit["subject"]))
        for path in set(commit["files"]):
            if not in_scope(path):
                continue
            changes[path] += 1
            if is_fix:
                fixes[path] += 1
    rows = []
    for path, count in changes.items():
        lines = file_lines(root / path) if (root / path).is_file() else 0
        rows.append(
            {
                "file": path,
                "changes": count,
                "lines": lines,
                "churn": count * lines,
                "fix_commits": fixes[path],
                "fix_share": round(fixes[path] / count, 3) if count else None,
            }
        )
    rows.sort(key=lambda r: (-r["churn"], r["file"]))
    return {
        "commits": len(commits),
        "files_touched": len(changes),
        "fix_commits": sum(1 for c in commits if FIX_SUBJECT.match(c["subject"])),
        "top": rows[:10],
    }


def shared_days(root: Path, days: int) -> dict[str, Any]:
    """(file, day) pairs with more than one distinct commit in the window."""
    commits = read_commits(root, days)
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for commit in commits:
        for path in set(commit["files"]):
            if in_scope(path):
                seen[(path, commit["day"])].add(commit["sha"])
    multi = {key: shas for key, shas in seen.items() if len(shas) > 1}
    per_file: Counter[str] = Counter(path for path, _ in multi)
    return {
        "file_days": len(seen),
        "shared_days": len(multi),
        "worst": [{"file": f, "days": n} for f, n in per_file.most_common(10)],
    }


# ---------------------------------------------------------------- the board


def collect(root: Path, caps: Any, windows: tuple[int, ...] = WINDOWS) -> dict[str, Any]:
    """Every number, for the tree at `root`."""
    return {
        "meta": {"root": str(root), **git_meta(root)},
        "frontend": {
            "grade_counts": grade_frontend(root),
            "boundary": frontend_boundary(root),
        },
        "backend": {
            "contracts": parse_importlinter(root / "backend" / ".importlinter"),
            "deferred_imports": count_deferred_imports(root),
            "files": backend_files(root),
        },
        "size": size_report(root, caps),
        "hotspots": {f"{d}d": hotspots(root, d) for d in windows},
        "shared_days": shared_days(root, max(windows)),
    }


def flatten(node: Any, prefix: str = "") -> dict[str, float]:
    """Scalar leaves of the board, keyed by dotted path — the delta's alphabet.

    List-valued rows (the top-10 tables) are skipped on purpose: the files in
    them are not the same set between two revisions, so a positional diff would
    compare two different things and read as a change.
    """
    flat: dict[str, float] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            flat.update(flatten(value, f"{prefix}.{key}" if prefix else key))
    elif isinstance(node, (int, float)) and not isinstance(node, bool):
        flat[prefix] = node
    return flat


def deltas(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    left, right = flatten(before), flatten(after)
    rows = []
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        if a is None and b is None:
            continue
        rows.append(
            {
                "metric": key,
                "before": a,
                "after": b,
                "delta": None if a is None or b is None else round(b - a, 3),
            }
        )
    return rows


def render_markdown(board: dict[str, Any], compare: dict[str, Any] | None = None) -> str:
    front = board["frontend"]["grade_counts"]
    boundary = board["frontend"]["boundary"]
    backend = board["backend"]
    contracts = backend["contracts"]
    size = board["size"]
    meta = board["meta"]

    out: list[str] = []
    out.append(f"# Architecture metrics — {meta['commit'] or 'unknown'}")
    out.append("")
    if meta["subject"]:
        out.append(f"{meta['date']} · {meta['subject']}")
        out.append("")

    out.append("## Frontend")
    out.append("")
    out.append("| metric | value |")
    out.append("|---|---|")
    out.append(f"| components (`.vue`) | {front['components']} |")
    for grade in "ABCD":
        out.append(
            f"| grade {grade} | {front['grades'][grade]} ({front['grade_pct'][grade]}%) |"
        )
    if boundary["violations"] is None:
        out.append(f"| frozen boundary violations | n/a — {boundary['why_missing']} |")
    else:
        out.append(
            f"| frozen boundary violations | {boundary['violations']} in {boundary['files']} files |"
        )
    out.append("")

    out.append("## Backend")
    out.append("")
    out.append("| metric | value |")
    out.append("|---|---|")
    if contracts is None:
        out.append("| frozen contract entries | n/a — no `backend/.importlinter` |")
    else:
        for name, count in contracts["per_contract"].items():
            out.append(f"| frozen: {name} | {count} |")
        out.append(f"| frozen contract entries, total | {contracts['total']} |")
    files = backend["files"]
    out.append(f"| imports inside a function | {backend['deferred_imports']} |")
    out.append(f"| files over 1000 lines (`backend/app`) | {files['files_over_1000']} |")
    out.append(f"| files over 1500 lines (`backend/app`) | {files['files_over_1500']} |")
    out.append(f"| `agent/chat.py` lines | {files['chat_py_lines']} |")
    out.append("")

    out.append("## File sizes (both ends)")
    out.append("")
    out.append("| tree | cap | over cap | excess lines |")
    out.append("|---|---|---|---|")
    for prefix, row in size["per_cap"].items():
        out.append(f"| `{prefix}` | {row['cap']} | {row['over_cap']} | {row['excess_lines']} |")
    out.append(f"| **total** | | **{size['offenders']}** | **{size['excess_lines']}** |")
    out.append("")

    for window, hot in board["hotspots"].items():
        out.append(f"## Hotspots, {window} (changes x lines)")
        out.append("")
        out.append(f"{hot['commits']} commits, {hot['files_touched']} files touched, "
                   f"{hot['fix_commits']} of them fixes.")
        out.append("")
        out.append("| file | changes | lines | changes x lines | fix commits | fix share |")
        out.append("|---|---|---|---|---|---|")
        for row in hot["top"]:
            share = "n/a" if row["fix_share"] is None else f"{row['fix_share'] * 100:.0f}%"
            out.append(
                f"| `{row['file']}` | {row['changes']} | {row['lines']} | {row['churn']}"
                f" | {row['fix_commits']} | {share} |"
            )
        out.append("")

    shared = board["shared_days"]
    out.append(f"## Same-day parallel edits, 90d")
    out.append("")
    out.append(
        f"{shared['shared_days']} of {shared['file_days']} file-days were touched by two or "
        "more distinct commits."
    )
    out.append("")
    out.append("| file | such days |")
    out.append("|---|---|")
    for row in shared["worst"]:
        out.append(f"| `{row['file']}` | {row['days']} |")
    out.append("")

    if compare:
        out.append(f"## Against {compare['ref']}")
        out.append("")
        moved = [d for d in compare["rows"] if d["delta"] not in (None, 0)]
        if not moved:
            out.append("No number moved.")
        else:
            out.append("| metric | before | after | delta |")
            out.append("|---|---|---|---|")
            for row in moved:
                before = "n/a" if row["before"] is None else row["before"]
                after = "n/a" if row["after"] is None else row["after"]
                sign = "+" if row["delta"] > 0 else ""
                out.append(f"| `{row['metric']}` | {before} | {after} | {sign}{row['delta']} |")
            missing = [d["metric"] for d in compare["rows"] if d["before"] is None]
            if missing:
                out.append("")
                out.append(
                    "Not measured before (the input did not exist at that revision): "
                    + ", ".join(f"`{m}`" for m in missing)
                    + "."
                )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ---------------------------------------------------------------- compare


@contextmanager
def base_worktree(root: Path, ref: str) -> Iterator[Path]:
    """A throwaway checkout of `ref`, so the same numbers can be read there.

    A worktree rather than `git archive`: the git metrics need history at that
    revision, and an archive has none. Removed in `finally` — a board that
    leaves worktrees behind is a board somebody turns off.
    """
    tmp = tempfile.mkdtemp(prefix="arch-metrics-")
    path = Path(tmp) / "tree"
    git(root, "worktree", "add", "--detach", str(path), ref)
    try:
        yield path
    finally:
        try:
            git(root, "worktree", "remove", "--force", str(path))
        finally:
            try:
                path.parent.rmdir()
            except OSError:
                pass


def compare(root: Path, ref: str, caps: Any, windows: tuple[int, ...]) -> dict[str, Any]:
    with base_worktree(root, ref) as base:
        before = collect(base, caps, windows)
    after = collect(root, caps, windows)
    return {
        "ref": ref,
        "before": before,
        "after": after,
        "rows": deltas(before, after),
    }


# ---------------------------------------------------------------- self-test


_FIXTURE = {
    "backend/.importlinter": """\
[importlinter]
root_package = app

[importlinter:contract:api-domain-core]
name = C1
type = layers
ignore_imports =
    # a comment between the entries is not one of them
    app.core.background -> app.api.deps
    app.domain.agent.chat -> app.api.deps

[importlinter:contract:routes-touch-no-models]
name = C2
type = forbidden
ignore_imports =
    app.api.routes.workspace -> app.domain.room_task.models
""",
    "backend/app/__init__.py": "",
    "backend/app/small.py": "def one():\n    from app.core.thing import read\n    return read\n",
    "backend/app/two.py": "import os\n\n\ndef two():\n    import json\n\n    def inner():\n        import re\n\n        return re\n\n    return json, inner\n",
    "frontend/import-boundary-baseline.json": (
        '{"_comment": "fixture", "files": {"src/components/Leaky.vue": 2, "src/views/V.vue": 1}}'
    ),
    # A: props only. B: app-chrome store only. C: the API directly.
    # C2: the API through a resolvable chain. D: the router.
    "frontend/src/api.ts": "export const api = {}\n",
    "frontend/src/services/thing.ts": "import { api } from '@/api'\nexport const thing = api\n",
    "frontend/src/utils/helper.ts": "import { thing } from '@/services/thing'\nexport const helper = thing\n",
    "frontend/src/components/A.vue": (
        "<template><div>{{ label }}</div></template>\n<script setup lang=\"ts\">\n"
        "defineProps<{ label: string }>()\n</script>\n"
    ),
    "frontend/src/components/B.vue": (
        "<script setup lang=\"ts\">\nconst title = usePageTitleStore()\n</script>\n"
        "<template><div>{{ title }}</div></template>\n"
    ),
    "frontend/src/components/C.vue": (
        "<script setup lang=\"ts\">\nimport { api } from '@/api'\n"
        "const go = () => api\n</script>\n<template><button @click=\"go\" /></template>\n"
    ),
    "frontend/src/components/C2.vue": (
        "<script setup lang=\"ts\">\nimport { helper } from '@/utils/helper'\n"
        "const go = () => helper\n</script>\n<template><button @click=\"go\" /></template>\n"
    ),
    "frontend/src/components/D.vue": (
        "<script setup lang=\"ts\">\nconst route = useRoute()\n"
        "const id = route.params.id\n</script>\n<template><div>{{ id }}</div></template>\n"
    ),
}


def _fixture_repo(root: Path, extra: dict[str, str] | None = None) -> None:
    """Plant the fixture and commit it, with a deterministic date so the git
    windows (30/90 days) contain it on any machine, at any time."""
    files = {**_FIXTURE, **(extra or {})}
    def write(relative: str, contents: str) -> None:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")

    for relative, contents in files.items():
        write(relative, contents)
    # 1600 lines over the backend cap, 1200 over 1000 but under 1500, and a
    # chat.py whose length the board reports separately.
    write("backend/app/big.py", "x = 1\n" * 1600)
    write("backend/app/mid.py", "x = 1\n" * 1200)
    write("backend/app/domain/agent/chat.py", "y = 1\n" * 40)
    write("frontend/src/views/V.vue", "<template><div /></template>\n")
    env = {
        "GIT_AUTHOR_NAME": "fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.com",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "chore: fixture"], cwd=root, check=True, env={**env, **_now_env()}
    )


def _now_env(days_ago: int = 5) -> dict[str, str]:
    """A commit date inside every window, without reaching for `date`."""
    import time

    stamp = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - days_ago * 86400))
    return {"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp}


def self_test() -> int:
    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    caps = load_size_caps(REPO_ROOT)

    with tempfile.TemporaryDirectory(prefix="arch-metrics-selftest-") as raw:
        root = Path(raw)

        # -- the parsers, on text, before any tree exists -------------------
        importlinter = root / "probe.importlinter"
        importlinter.write_text(_FIXTURE["backend/.importlinter"], encoding="utf-8")
        parsed = parse_importlinter(importlinter)
        check("frozen entries in the first contract", parsed["per_contract"]["api-domain-core"], 2)
        check(
            "frozen entries in the second contract",
            parsed["per_contract"]["routes-touch-no-models"],
            1,
        )
        check("frozen entries in total", parsed["total"], 3)
        check("no config is None, not zero", parse_importlinter(root / "absent"), None)
        check("an empty file counts nothing", parse_importlinter(root / "empty") is None, True)

        try:
            _fixture_repo(root)
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            print(f"SELF-TEST FAIL: could not build the fixture repository: {exc}")
            return 1

        board = collect(root, caps)

        # -- frontend grading ----------------------------------------------
        grades = board["frontend"]["grade_counts"]
        check("components counted", grades["components"], 6)
        # A.vue is props alone and views/V.vue reads nothing at all: both A.
        check("A: nothing global at all", grades["grades"]["A"], 2)
        check("B: app-chrome store only", grades["grades"]["B"], 1)
        # C.vue imports @/api; C2.vue reaches it through utils/helper.ts ->
        # services/thing.ts -> api.ts, which is the axis a naive count misses.
        check("C: api directly and through a chain", grades["grades"]["C"], 2)
        check("D: the router", grades["grades"]["D"], 1)
        check("a share is a percentage, not a fraction", grades["grade_pct"]["A"], 33.3)
        check(
            "the boundary baseline is read, not re-derived",
            board["frontend"]["boundary"],
            {"files": 2, "violations": 3},
        )

        # -- backend ---------------------------------------------------------
        check("frozen contract entries", board["backend"]["contracts"]["total"], 3)
        # small.py has one inside a function; two.py has one inside a nested
        # function and one at module level, which does not count.
        check("imports inside a function", board["backend"]["deferred_imports"], 3)
        check("files over 1000 lines", board["backend"]["files"]["files_over_1000"], 2)
        check("files over 1500 lines", board["backend"]["files"]["files_over_1500"], 1)
        check("chat.py lines", board["backend"]["files"]["chat_py_lines"], 40)

        # -- file sizes ------------------------------------------------------
        backend_cap = board["size"]["per_cap"]["backend/app/"]
        check("backend offenders", backend_cap["over_cap"], 1)
        check("backend excess lines", backend_cap["excess_lines"], 100)
        frontend_cap = board["size"]["per_cap"]["frontend/src/"]
        check("nothing in the fixture frontend is over the cap", frontend_cap["over_cap"], 0)
        check(
            "the total is the sum, not one tree",
            board["size"]["offenders"],
            1,
        )

        # -- hotspots --------------------------------------------------------
        # chat.py is planted and committed; the fixture's only commit is a
        # `chore:`, so it is a change and not a fix.
        hot = board["hotspots"]["30d"]
        check("the fixture has one commit in the window", hot["commits"], 1)
        check("that commit is not a fix", hot["fix_commits"], 0)
        check(
            "the biggest file in scope tops the list",
            hot["top"][0]["file"],
            "backend/app/big.py",
        )
        check("its change count", hot["top"][0]["changes"], 1)
        check("its fix share", hot["top"][0]["fix_share"], 0.0)
        check("nothing is out of scope", board["shared_days"]["shared_days"], 0)

        # -- a second day of history, so the fix ratio and the shared days
        #    have something to be about --------------------------------------
        env = {
            "GIT_AUTHOR_NAME": "fixture",
            "GIT_AUTHOR_EMAIL": "fixture@example.com",
            "GIT_COMMITTER_NAME": "fixture",
            "GIT_COMMITTER_EMAIL": "fixture@example.com",
            **_now_env(1),
        }
        (root / "frontend/src/api.ts").write_text("export const api = {}\nexport const b = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "fix(api): repair it"], cwd=root, check=True, env=env
        )
        (root / "frontend/src/api.ts").write_text(
            "export const api = {}\nexport const b = 1\nexport const c = 2\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-qm", "fix(api): repair it again"], cwd=root, check=True, env=env
        )

        board = collect(root, caps)
        hot = board["hotspots"]["30d"]
        row = next(r for r in hot["top"] if r["file"] == "frontend/src/api.ts")
        # Three commits touch api.ts: the fixture, then two fixes on one day.
        check("every commit that touched a file is one change", row["changes"], 3)
        check("the fixes are told apart from it", row["fix_commits"], 2)
        check("the fix share is a share", row["fix_share"], 0.667)
        check("the file's own size is read from the tree", row["lines"], 3)
        check("changes x lines", row["churn"], 9)
        check("same-day parallel edits are counted", board["shared_days"]["shared_days"], 1)
        check(
            "and attributed to the file",
            board["shared_days"]["worst"][0]["file"],
            "frontend/src/api.ts",
        )

        # -- compare, end to end, through the real CLI ------------------------
        head = git(root, "rev-parse", "HEAD").strip()
        first = git(root, "rev-list", "--max-parents=0", "HEAD").strip()
        # A directory outside every measured prefix, so writing the board
        # cannot change what the next board measures.
        where = root / "out"
        where.mkdir()
        capped = where / "board.json"
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--json", str(capped), "--md", str(where / "board.md")],
            capture_output=True,
            text=True,
        )
        check("a board on the fixture exits 0", result.returncode, 0)
        check("... and writes JSON", capped.is_file(), True)

        payload = where / "both.json"
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--compare", first, "--json", str(payload), "--md", str(where / "both.md")],
            capture_output=True,
            text=True,
        )
        check("compare exits 0", result.returncode, 0)
        if payload.is_file():
            both = json.loads(payload.read_text(encoding="utf-8"))
            rows = {row["metric"]: row for row in both["compare"]["rows"]}
            # The base tree sees the fixture commit only; HEAD sees two more,
            # which is exactly what a delta is for.
            grew = rows["hotspots.30d.commits"]
            check("the compare knows which side is which", grew["before"], 1)
            check("... and the after side", grew["after"], 3)
            check("... and shows the difference as a number", grew["delta"], 2)
            check("HEAD is the after side", both["meta"]["commit"][:12], head[:12])
            check(
                "the base tree's numbers stand alone",
                both["compare"]["before"]["meta"]["commit"][:12],
                first[:12],
            )
            check(
                "the board is still in the file next to the compare",
                both["board"]["frontend"]["grade_counts"]["components"],
                6,
            )
        else:
            failures.append("compare wrote no JSON")
        # The worktree is gone afterwards: a board must not leave one behind.
        check(
            "no worktree is left behind",
            len(git(root, "worktree", "list").splitlines()),
            1,
        )

        # -- a ref this checkout does not have is exit 2, never a quiet pass ---
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--compare", "no-such-ref"],
            capture_output=True,
            text=True,
        )
        check("a missing ref exits 2", result.returncode, 2)
        check("... and says so", "cannot judge" in result.stdout + result.stderr, True)

    for failure in failures:
        print(f"SELF-TEST FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "PASS: arch-metrics self-test (grades A/B/C/D incl. a transitive api reach, "
        "frozen contract entries, imports inside a function, cap offenders and "
        "excess lines, hotspot churn and fix share, same-day parallel edits, and "
        "a compare that leaves no worktree behind)"
    )
    return 0


# ---------------------------------------------------------------- entry point


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to measure")
    parser.add_argument("--compare", default=None, help="a git ref to measure too")
    parser.add_argument("--out", default=None, help="directory for arch-metrics.{json,md}")
    parser.add_argument("--json", default=None, help="write the JSON board here")
    parser.add_argument("--md", default=None, help="write the markdown board here")
    parser.add_argument("--days", default=",".join(str(d) for d in WINDOWS))
    parser.add_argument("--quiet", action="store_true", help="write the files, print nothing")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    if not (root / ".git").exists():
        print(f"cannot judge: {root} is not a git checkout", file=sys.stderr)
        return 2
    try:
        windows = tuple(int(part) for part in args.days.split(",") if part.strip())
    except ValueError:
        print(f"cannot judge: --days {args.days!r} is not a list of numbers", file=sys.stderr)
        return 2

    try:
        caps = load_size_caps(root)
    except ImportError as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return 2

    try:
        board = collect(root, caps, windows)
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"cannot judge: git failed: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return 2

    payload = dict(board)
    markdown = render_markdown(board)
    if args.compare:
        try:
            compared = compare(root, args.compare, caps, windows)
        except subprocess.CalledProcessError as exc:
            print(
                f"cannot judge: {args.compare} is not a ref this checkout has "
                f"({exc.stderr.strip().splitlines()[-1] if exc.stderr else exc})",
                file=sys.stderr,
            )
            return 2
        payload = {
            "meta": board["meta"],
            "board": board,
            "compare": {"ref": compared["ref"], "before": compared["before"],
                        "rows": compared["rows"]},
        }
        markdown = render_markdown(board, {"ref": compared["ref"], "rows": compared["rows"]})

    out_dir = Path(args.out) if args.out else root
    json_path = Path(args.json) if args.json else out_dir / "arch-metrics.json"
    md_path = Path(args.md) if args.md else out_dir / "arch-metrics.md"
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md_path.write_text(markdown, encoding="utf-8")

    if not args.quiet:
        print(markdown)
        print(f"json: {json_path}\nmd:   {md_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
