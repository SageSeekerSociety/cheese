#!/usr/bin/env python3
"""catalog-ratchet.py — a component that can be shown alone is shown in the catalog.

    python3 .claude/scripts/catalog-ratchet.py               check (exit 1 on a new uncatalogued component)
    python3 .claude/scripts/catalog-ratchet.py --update      the pending list may only shrink
    python3 .claude/scripts/catalog-ratchet.py --init        write the first pending list (no file yet)
    python3 .claude/scripts/catalog-ratchet.py --list        every component, its grade, catalogued or not
    python3 .claude/scripts/catalog-ratchet.py --json        one JSON record on stdout, same exit code
    python3 .claude/scripts/catalog-ratchet.py --self-test   prove it catches what it claims

WHY A GATE AND NOT A WARNING. The preview site (`/demo/catalog`) is the only
place a component is rendered on its own, and `catalog.spec.ts` mounts every
entry with only the plugins it declares — so a catalogued component that stops
rendering alone is a red test. A component that COULD be catalogued but is not
has no such watcher: it is provably standalone today (grade A) and nothing
will notice the day it stops being. This used to be a warning inside
`scene-ratchet.py`, and a warning count is a number people learn to read past;
it also counted against `catalog.ts` alone (missing the entries split into
`catalogAccept.ts`, `catalogBase.ts`, ...) and looked only at pages and panels.

THE RULE: every `.vue` under `frontend/src` (the preview site itself,
`src/views/demo/`, excluded) that `frontend_grade.py` grades A is either in the
catalog or on the `pending` list in `frontend/catalog-baseline.json`. `pending`
is today's backlog, and it may only shrink:

  * A grade-A component that is in neither FAILS. That is a new component, or
    an old one that just became A — the moment it can be shown alone is the
    moment to show it. The steps are in `frontend/AGENTS.md`, "The component
    preview site".
  * A `pending` entry that no longer describes the tree — the component is
    catalogued now, is gone, or is not grade A any more — is reported and
    `--update` drops it, but it does NOT fail the check. That is the same
    choice `scene-ratchet.py` makes for a scene that got better than its
    baseline: the debt was paid in the tree, and failing the commit that paid
    it would punish the payment and make the cleanup a second commit somebody
    has to remember. The stale line is printed with the command that clears it
    and the `--json` record carries it in `stale`, so the board shows it.
  * A catalogued component that is NOT grade A is not this check's business:
    `catalog.spec.ts` mounts it and is the authority on whether it renders.
    `--list` shows it so nobody has to wonder.

`--update` only ever removes from `pending`. It never adds, and it refuses to
write at all while there is a grade-A component in neither list: adding one
would turn "the backlog may only shrink" into "the backlog is whatever it is
today". `--init` writes the first list and only when the file does not exist;
deleting the file to re-run it is visible in the diff.

WHAT "CATALOGUED" MEANS. Read from the code, not from the `file:` field — a
label can be wrong, an import cannot. A component is catalogued when a `.ts`
under `frontend/src/views/demo/` (spec and test files excluded: a spec that
imports a component to test it does not put it on the site) both
value-imports it (`import X from '@/components/X.vue'`, or relative
`./`/`../`, resolved the way `frontend_grade.resolve_spec` resolves `@/`) and
uses that binding as an entry's component (`component: X`, also
`component: X as ...` or `component: markRaw(X)`). An import that no entry
renders is not an entry.

STANDALONE means grade A from `.claude/scripts/frontend_grade.py` — the same
function `arch-metrics.py` reports and `scene-ratchet.py` gates on, so the
three cannot disagree about a component. It is a regex estimate; its blind
spots are documented there.

Exit codes: 0 nothing to fix, 1 a grade-A component is uncatalogued and not
pending (or `--update`/`--init` refused), 2 could not judge (no `frontend/src/`
under `--root`, no readable baseline, an unreadable file). 2 is never a pass.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ratchet_report import as_json, cannot_judge, emit, verdict as json_verdict

REPO_ROOT = Path(__file__).resolve().parents[2]

CHECK_ID = "catalog-ratchet"

#: The preview site: its own components are not subjects, its `.ts` files are
#: where the catalog lives.
DEMO_DIR = "frontend/src/views/demo/"

DEFAULT_BASELINE = "frontend/catalog-baseline.json"

#: A spec file puts nothing on the site, whatever it imports.
SPEC = re.compile(r"\.(spec|test)\.(ts|js)$")

#: An import with its clause: `import A from 'x'`, `import A, { b } from 'x'`.
IMPORT_CLAUSE = re.compile(
    r"""import\s+(type\s+)?([^'"]*?)\s+from\s+['"]([^'"]+)['"]""", re.DOTALL
)

#: Where the steps for adding an entry are written down.
HOW_TO = "frontend/AGENTS.md, section \"The component preview site\""


class Unjudgeable(Exception):
    """The tree or the baseline could not be read. Exit 2, never a pass."""


# --------------------------------------------------------------------- loading


def load_frontend_grade() -> Any:
    """The grader, from THIS script's directory and never from `--root`.

    A copy of the grade here would be a second answer to "does this run
    alone", and `scene-ratchet.py` loads it the same way for the same reason.
    """
    source = Path(__file__).resolve().parent / "frontend_grade.py"
    spec = importlib.util.spec_from_file_location("_frontend_grade", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable script
        raise Unjudgeable(f"cannot load the grader at {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_frontend_grade"] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ the tree


def key_of(rel: str) -> str:
    """`frontend/src/x/Y.vue` -> `src/x/Y.vue`: baseline keys are frontend-relative."""
    if not rel.startswith("frontend/"):
        raise ValueError(f"not under frontend/: {rel}")
    return rel[len("frontend/") :]


def subject_paths(root: Path) -> list[str]:
    """Every `.vue` under `frontend/src` outside the preview site, repo-relative."""
    src = root / "frontend" / "src"
    if not src.is_dir():
        raise Unjudgeable(f"no frontend/src/ under {root}")
    out = []
    for path in sorted(src.rglob("*.vue")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not rel.startswith(DEMO_DIR):
            out.append(rel)
    return out


def default_local(clause: str) -> str | None:
    """The default binding of an import clause: `A, { b }` -> `A`; `{ b }` -> None.

    `{ default as A }` is a default import too, spelled the long way.
    """
    clause = clause.strip()
    if clause.startswith("*"):
        return None
    head, _, brace = clause.partition("{")
    head = head.strip().rstrip(",").strip()
    if head:
        return head
    match = re.search(r"\bdefault\s+as\s+([A-Za-z_$][\w$]*)", brace)
    return match.group(1) if match else None


def catalogued(root: Path, grade_module: Any) -> dict[str, list[str]]:
    """`{component rel: [catalog files that render it]}`.

    A component is catalogued by a non-spec `.ts` under the preview site that
    value-imports it AND uses that binding as an entry's `component:`.
    """
    src = root / "frontend" / "src"
    demo = root / DEMO_DIR
    found: dict[str, list[str]] = {}
    if not demo.is_dir():
        return found
    for path in sorted(demo.rglob("*.ts")):
        if not path.is_file() or SPEC.search(path.name):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Unjudgeable(f"cannot read {path}: {exc}") from exc
        here = path.relative_to(root).as_posix()
        for type_only, clause, spec in IMPORT_CLAUSE.findall(text):
            if type_only or not spec.endswith(".vue"):
                continue
            local = default_local(clause)
            if local is None:
                continue
            uses = re.compile(
                r"\bcomponent\s*:\s*(?:markRaw\s*\(\s*)?" + re.escape(local) + r"(?![\w$])"
            )
            if not uses.search(text):
                continue  # imported, but no entry renders it
            resolved = grade_module.resolve_spec(spec, path, src)
            if resolved is None:
                continue
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            found.setdefault(rel, []).append(here)
    return found


def grade_all(root: Path, grade_module: Any) -> dict[str, Any]:
    """`{rel: Grade}` for every subject — one `api_reach` for all."""
    reach = grade_module.api_reach(root)
    grades = {}
    for rel in subject_paths(root):
        try:
            grades[rel] = grade_module.grade_component(root, root / rel, reach)
        except OSError as exc:
            raise Unjudgeable(f"cannot read {rel}: {exc}") from exc
    return grades


# ------------------------------------------------------------------ the ratchet


@dataclass
class Verdict:
    """What the tree says about the pending list."""

    #: grade A, uncatalogued, not pending — what the ratchet blocks
    new: list[str] = field(default_factory=list)
    #: grade A, uncatalogued, pending — the backlog
    pending: list[str] = field(default_factory=list)
    #: (key, why): a pending entry that no longer describes the tree
    stale: list[tuple[str, str]] = field(default_factory=list)
    #: grade A and catalogued
    shown: list[str] = field(default_factory=list)
    #: catalogued but not grade A: catalog.spec.ts's business, listed only
    catalogued_not_a: list[tuple[str, str]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.new


def judge(pending: frozenset[str], grades: dict[str, Any], shown: set[str]) -> Verdict:
    """Compare the tree against the pending list. Pure, so it is testable."""
    verdict = Verdict()
    for rel in sorted(grades):
        key = key_of(rel)
        grade = grades[rel]
        if rel in shown:
            if grade.standalone:
                verdict.shown.append(key)
            else:
                verdict.catalogued_not_a.append((key, grade.letter))
            continue
        if not grade.standalone:
            continue
        if key in pending:
            verdict.pending.append(key)
        else:
            verdict.new.append(key)
    for key in sorted(pending):
        rel = f"frontend/{key}"
        if rel not in grades:
            verdict.stale.append((key, "is no longer in the tree"))
        elif rel in shown:
            verdict.stale.append((key, "is in the catalog now"))
        elif not grades[rel].standalone:
            verdict.stale.append((key, f"is not grade A any more (now {grades[rel].letter})"))
    return verdict


# ------------------------------------------------------------------- the I/O


def read_baseline(path: Path) -> frozenset[str]:
    """The pending list. Missing or malformed is a 2: without it a new
    component cannot be told from an old one."""
    if not path.is_file():
        raise Unjudgeable(
            f"no baseline at {path} — this check cannot tell a new component from "
            f"a pending one without it (run with --init to write the first one)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Unjudgeable(f"cannot read {path}: {exc}") from exc
    pending = data.get("pending") if isinstance(data, dict) else None
    if not isinstance(pending, list):
        raise Unjudgeable(f'{path} needs a "pending" list')
    for key in pending:
        if not isinstance(key, str) or not key.startswith("src/") or not key.endswith(".vue"):
            raise Unjudgeable(f"{path} has a key that is not a frontend-relative .vue: {key!r}")
    return frozenset(pending)


def write_baseline(path: Path, pending: frozenset[str] | set[str]) -> None:
    """`_comment` first, so the rule travels with the file it governs."""
    payload = {
        "_comment": (
            "Grade-A components (frontend_grade.py) that are not in the /demo/catalog "
            "preview site yet, frozen by .claude/scripts/catalog-ratchet.py. This list "
            "may only shrink: a grade-A component that is neither catalogued nor listed "
            "here fails the check. Add a catalog entry (frontend/AGENTS.md, \"The "
            "component preview site\") and run `pnpm run lint:catalog:update` to drop "
            "it from here; --update never adds."
        ),
        "pending": sorted(pending),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_report(verdict: Verdict, pending: frozenset[str]) -> str:
    """The human half: what is wrong, why, and what to do."""
    lines: list[str] = []
    if verdict.new:
        lines.append("Grade-A components that are not in /demo/catalog (this is what the ratchet blocks):")
        lines.extend(f"  {key}" for key in verdict.new)
        lines.append("")
        lines.append("A component that renders from props alone can be shown alone, so it")
        lines.append("goes into the preview site the day it becomes grade A. Add an entry in")
        lines.append("frontend/src/views/demo/catalog*.ts (import the .vue, set it as an")
        lines.append("entry's `component:`) and its props, then run")
        lines.append("`pnpm exec vitest run src/views/demo/catalog.spec.ts`. The steps:")
        lines.append(f"{HOW_TO}.")
        lines.append("The pending list in frontend/catalog-baseline.json is not an option:")
        lines.append("it only shrinks, and --update refuses to add to it.")
        lines.append("")
    if verdict.stale:
        lines.append("Pending entries that no longer describe the tree — cross them off:")
        for key, why in verdict.stale:
            lines.append(f"  {key}: {why}")
        lines.append("")
        lines.append("Run: pnpm run lint:catalog:update  (then commit frontend/catalog-baseline.json)")
        lines.append("")
    lines.append(
        f"{len(verdict.shown)} grade-A component(s) catalogued, {len(verdict.pending)} pending, "
        f"{len(verdict.new)} new; pending list has {len(pending)}"
    )
    return "\n".join(lines)


def run(root: Path, baseline_path: Path, *, update: bool, init: bool, listing: bool) -> int:
    """Judge the tree at `root` against `baseline_path` and print the answer."""
    try:
        grade_module = load_frontend_grade()
        grades = grade_all(root, grade_module)
        shown_by = catalogued(root, grade_module)
        shown = set(shown_by)
        if init:
            if baseline_path.exists():
                print(
                    f"refusing to --init: {baseline_path} exists. The pending list only "
                    "shrinks; use --update to cross entries off.",
                    file=sys.stderr,
                )
                return 1
            first = {key_of(rel) for rel, g in grades.items() if g.standalone and rel not in shown}
            write_baseline(baseline_path, first)
            print(f"baseline written: {len(first)} pending — {baseline_path}")
            return 0
        pending = read_baseline(baseline_path)
    except Unjudgeable as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        cannot_judge(CHECK_ID, str(exc))

    verdict = judge(pending, grades, shown)

    if update:
        if verdict.new:
            print("refusing to update: the pending list may only shrink, and these are", file=sys.stderr)
            print("grade-A components in neither the catalog nor the list:", file=sys.stderr)
            for key in verdict.new:
                print(f"  {key}", file=sys.stderr)
            print(f"\nCatalog them ({HOW_TO}), then run --update again.", file=sys.stderr)
            return 1
        stale = {key for key, _ in verdict.stale}
        next_pending = pending - stale
        write_baseline(baseline_path, next_pending)
        print(
            f"baseline updated: {len(next_pending)} pending "
            f"({len(stale)} crossed off) — {baseline_path}"
        )
        return 0

    if as_json():
        # The unit is a component, and one costs at most one: `actual` counts
        # every grade-A component the catalog does not show (pending or new),
        # `frozen` is the length of the list that allows them.
        emit(
            json_verdict(
                check_id=CHECK_ID,
                ok=verdict.ok,
                actual=len(verdict.pending) + len(verdict.new),
                frozen=len(pending),
                stale=[
                    {"file": key, "frozen": 1, "actual": 0, "why": why}
                    for key, why in verdict.stale
                ],
                details=[{"file": key, "grade": "A", "status": "new"} for key in verdict.new]
                + [{"file": key, "grade": "A", "status": "pending"} for key in verdict.pending],
            )
        )
        return 0 if verdict.ok else 1

    if listing:
        new, pend = set(verdict.new), set(verdict.pending)
        for rel in sorted(grades):
            key = key_of(rel)
            letter = grades[rel].letter
            if rel in shown:
                note = "catalogued" if letter == "A" else (
                    "catalogued, not grade A — catalog.spec.ts decides whether it renders")
                where = ", ".join(sorted(set(shown_by[rel])))
                print(f"{letter} {key}  ({note}: {where})")
            elif key in new:
                print(f"{letter} {key}  (NOT catalogued, not pending)")
            elif key in pend:
                print(f"{letter} {key}  (pending)")
            else:
                print(f"{letter} {key}")
        print()
    print(format_report(verdict, pending))
    return 0 if verdict.ok else 1


# ------------------------------------------------------------------ self-test

_A = (
    '<script setup lang="ts">\ndefineProps<{ n: number }>()\n</script>\n'
    "<template><div>{{ n }}</div></template>\n"
)
_C = (
    "<script setup lang=\"ts\">\nimport { api } from '@/api'\napi.get()\n</script>\n"
    "<template><div /></template>\n"
)

#: Small on purpose: one component per way of being (not) catalogued.
FIXTURE: dict[str, str] = {
    "frontend/src/api.ts": "export const api = { get: () => fetch('/x') }\n",
    "frontend/src/components/Shown.vue": _A,  # catalogued via @/
    "frontend/src/components/Rel.vue": _A,  # catalogued via a relative path
    "frontend/src/components/Casted.vue": _A,  # `component: X as Component`
    "frontend/src/components/Waiting.vue": _A,  # pending
    "frontend/src/components/Fetching.vue": _C,  # not A: not a candidate
    "frontend/src/components/ShownC.vue": _C,  # catalogued, not A: --list only
    "frontend/src/components/OnlyImported.vue": _A,  # pending; imported, never rendered
    "frontend/src/components/OnlySpec.vue": _A,  # pending; only a spec renders it
    "frontend/src/views/demo/DemoThing.vue": _A,  # the preview site itself: not a subject
    "frontend/src/views/demo/catalog.ts": (
        "import type { Component } from 'vue'\n"
        "import Shown from '@/components/Shown.vue'\n"
        "import ShownC from '@/components/ShownC.vue'\n"
        "import OnlyImported from '@/components/OnlyImported.vue'\n"
        "import { MORE } from './catalogMore'\n"
        "export const CATALOG = [\n"
        "  { id: 'shown', file: 'src/components/Shown.vue', component: Shown },\n"
        "  { id: 'c', component: ShownC },\n"
        "  { id: 'liar', file: 'src/components/Waiting.vue', component: Shown },\n"
        "  ...MORE,\n"
        "]\n"
        "void OnlyImported\n"
    ),
    "frontend/src/views/demo/catalogMore.ts": (
        "import Rel from '../../components/Rel.vue'\n"
        "import Casted from '@/components/Casted.vue'\n"
        "export const MORE = [\n"
        "  { id: 'rel', component: Rel },\n"
        "  { id: 'casted', component: Casted as unknown as object },\n"
        "]\n"
    ),
    "frontend/src/views/demo/catalog.spec.ts": (
        "import OnlySpec from '@/components/OnlySpec.vue'\n"
        "const entry = { component: OnlySpec }\n"
    ),
}

FIXTURE_PENDING = [
    "src/components/OnlyImported.vue",
    "src/components/OnlySpec.vue",
    "src/components/Waiting.vue",
]


def _fixture(root: Path, changes: dict[str, str] | None = None) -> None:
    """Write FIXTURE into `root`, then apply `changes` (a value of '' deletes)."""
    files = dict(FIXTURE)
    files.update(changes or {})
    for rel, body in files.items():
        path = root / rel
        if body == "":
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _fixture_baseline(root: Path, pending: list[str] | None = None) -> Path:
    path = root / "frontend" / "catalog-baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = FIXTURE_PENDING if pending is None else pending
    path.write_text(json.dumps({"_comment": "x", "pending": keys}, indent=2) + "\n", encoding="utf-8")
    return path


def self_test() -> int:
    """Drive the real CLI over fixture trees and require each exit code."""
    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def run_cli(root: Path, baseline: Path, *args: str) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--baseline", str(baseline), *args],
            capture_output=True, text=True,
        )
        # A crash is never a result: it exits 1 and would satisfy every "must
        # fail" case for a reason unrelated to the rule.
        if "Traceback" in result.stderr:
            failures.append(f"crashed: {' '.join(args) or 'check'}\n{result.stderr.strip()}")
        return result

    def pending_of(path: Path) -> list[str]:
        return json.loads(path.read_text(encoding="utf-8"))["pending"]

    with tempfile.TemporaryDirectory(prefix="catalog-ratchet-selftest-") as raw:
        root = Path(raw)
        _fixture(root)
        baseline = _fixture_baseline(root)

        # -- the fixture's own list passes, and catalogued is import + component
        result = run_cli(root, baseline)
        check("the fixture's own pending list passes", result.returncode, 0)
        listing = run_cli(root, baseline, "--list").stdout
        check("@/ import + component: is catalogued",
              "A src/components/Shown.vue  (catalogued" in listing, True)
        check("a relative import is catalogued",
              "A src/components/Rel.vue  (catalogued" in listing, True)
        check("`component: X as ...` is catalogued",
              "A src/components/Casted.vue  (catalogued" in listing, True)
        check("catalogued but not A is shown in --list",
              "C src/components/ShownC.vue  (catalogued, not grade A" in listing, True)
        check("a `file:` label naming it does not catalogue it",
              "A src/components/Waiting.vue  (pending)" in listing, True)
        check("an import no entry renders does not catalogue it",
              "A src/components/OnlyImported.vue  (pending)" in listing, True)
        check("a spec file's import does not catalogue it",
              "A src/components/OnlySpec.vue  (pending)" in listing, True)
        check("the preview site's own components are not subjects",
              "DemoThing.vue" in listing, False)

        # -- 1. a new grade-A component, uncatalogued, not pending -----------
        _fixture(root, {"frontend/src/components/Brand.vue": _A})
        result = run_cli(root, baseline)
        check("a new grade-A component outside the catalog fails", result.returncode, 1)
        check("and is named", "  src/components/Brand.vue" in result.stdout, True)
        check("and is pointed at the steps", "The component preview site" in result.stdout, True)
        result = run_cli(root, baseline, "--update")
        check("--update refuses while one is new", result.returncode, 1)
        check("and does not add it", "src/components/Brand.vue" in pending_of(baseline), False)
        record = json.loads(run_cli(root, baseline, "--json").stdout)
        check("--json says fail", record["status"], "fail")
        check("--json counts every uncatalogued A", record["actual"], 4)
        check("--json frozen is the list length", record["frozen"], 3)
        # ... and the same component, once catalogued, passes
        _fixture(root, {
            "frontend/src/components/Brand.vue": _A,
            "frontend/src/views/demo/catalogBrand.ts": (
                "import Brand from '@/components/Brand.vue'\n"
                "export const B = [{ id: 'brand', component: Brand }]\n"
            ),
        })
        check("catalogued, the new component passes", run_cli(root, baseline).returncode, 0)
        (root / "frontend/src/components/Brand.vue").unlink()
        (root / "frontend/src/views/demo/catalogBrand.ts").unlink()

        # -- 2. a component that becomes A is new too -------------------------
        _fixture(root, {"frontend/src/components/Fetching.vue": _A})
        check("a component that just became A fails", run_cli(root, baseline).returncode, 1)
        _fixture(root)

        # -- 3. stale pending: catalogued now, gone, not A any more ----------
        _fixture(root, {
            "frontend/src/views/demo/catalogWaiting.ts": (
                "import Waiting from '@/components/Waiting.vue'\n"
                "export const W = [{ id: 'w', component: Waiting }]\n"
            ),
            "frontend/src/components/OnlySpec.vue": "",
            "frontend/src/components/OnlyImported.vue": _C,
        })
        result = run_cli(root, baseline)
        check("stale pending entries do not fail", result.returncode, 0)
        check("one catalogued now is reported",
              "src/components/Waiting.vue: is in the catalog now" in result.stdout, True)
        check("one gone is reported",
              "src/components/OnlySpec.vue: is no longer in the tree" in result.stdout, True)
        check("one no longer A is reported",
              "src/components/OnlyImported.vue: is not grade A any more (now C)" in result.stdout, True)
        check("and the command that clears them", "pnpm run lint:catalog:update" in result.stdout, True)
        record = json.loads(run_cli(root, baseline, "--json").stdout)
        check("--json carries them as stale", sorted(s["file"] for s in record["stale"]),
              sorted(FIXTURE_PENDING))
        result = run_cli(root, baseline, "--update")
        check("--update crosses them off", (result.returncode, pending_of(baseline)), (0, []))
        (root / "frontend/src/views/demo/catalogWaiting.ts").unlink()
        _fixture(root)
        # ...and now that the list is empty, the same tree's backlog is new.
        check("a crossed-off entry cannot come back silently", run_cli(root, baseline).returncode, 1)
        result = run_cli(root, baseline, "--update")
        check("--update never re-adds it", (result.returncode, pending_of(baseline)), (1, []))

        # -- 4. --init writes only when there is no file ---------------------
        result = run_cli(root, baseline, "--init")
        check("--init refuses over an existing file", result.returncode, 1)
        baseline.unlink()
        check("no baseline is a 2", run_cli(root, baseline).returncode, 2)
        check("no baseline is a 2 in --update too", run_cli(root, baseline, "--update").returncode, 2)
        result = run_cli(root, baseline, "--init")
        check("--init writes the current backlog", (result.returncode, pending_of(baseline)),
              (0, FIXTURE_PENDING))
        check("which then passes", run_cli(root, baseline).returncode, 0)
        baseline.write_text("{not json", encoding="utf-8")
        check("an unreadable baseline is a 2", run_cli(root, baseline).returncode, 2)
        _fixture_baseline(root)

        # -- 5. a relative root grades the same as an absolute one -----------
        #    `api_reach` once compared absolute edges against a relative root,
        #    dropped every edge and graded everything A.
        grade_module = load_frontend_grade()
        relative = Path(os.path.relpath(root))
        check("the root is really relative here", relative.is_absolute(), False)
        fetching = root / "frontend/src/components/Fetching.vue"
        check("a relative root still sees the API layer",
              grade_module.grade_component(relative, fetching, grade_module.api_reach(relative)).letter,
              "C")
        check("as does an absolute one",
              grade_module.grade_component(root, fetching, grade_module.api_reach(root)).letter, "C")

        # -- 6. a wrong root is a 2, never a pass -----------------------------
        empty = root / "empty"
        empty.mkdir()
        wrong = run_cli(empty, baseline)
        check("a root with no frontend/src is a 2", wrong.returncode, 2)
        record = json.loads(run_cli(empty, baseline, "--json").stdout)
        check("and says so in --json", record["status"], "cannot_judge")

    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print(f"  {line}")
        return 1
    print(
        "PASS: catalog-ratchet self-test (a new uncatalogued grade-A component fails "
        "and so does one that just became A; catalogued means import + component: "
        "via @/, a relative path and an `as` cast, never a `file:` label, an unused "
        "import or a spec; stale pending entries — catalogued, gone, no longer A — "
        "are reported and crossed off; --update never adds and refuses over a new "
        "one; --init only without a file; a relative root grades like an absolute "
        "one; no baseline, a bad baseline and a wrong root are a 2)"
    )
    return 0


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to judge")
    parser.add_argument("--baseline", default=None, help=f"default: {DEFAULT_BASELINE}")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--update", action="store_true", help="drop pending entries that are done")
    mode.add_argument("--init", action="store_true", help="write the first pending list (no file yet)")
    mode.add_argument("--list", action="store_true", help="print every component with its grade")
    mode.add_argument("--self-test", action="store_true")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON record instead of the report (the collector reads it)",
    )
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    # Resolved: the grader compares absolute import targets against this root.
    root = Path(args.root).resolve()
    baseline_path = Path(args.baseline) if args.baseline else root / DEFAULT_BASELINE
    try:
        return run(root, baseline_path, update=args.update, init=args.init, listing=args.list)
    except LookupError as exc:
        # The grader refuses a root without `frontend/src`.
        print(f"cannot judge: {exc}", file=sys.stderr)
        cannot_judge(CHECK_ID, str(exc))


if __name__ == "__main__":
    sys.exit(main())
