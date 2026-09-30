#!/usr/bin/env python3
"""scene-ratchet.py — a scene that runs standalone may not stop running standalone.

    python3 .claude/scripts/scene-ratchet.py               check (exit 1 on new debt)
    python3 .claude/scripts/scene-ratchet.py --update      the baseline may only grow
    python3 .claude/scripts/scene-ratchet.py --list        every scene, its grade, its reasons
    python3 .claude/scripts/scene-ratchet.py --json        one JSON record on stdout, same exit code
    python3 .claude/scripts/scene-ratchet.py --self-test   prove it catches what it claims

WHY A GATE AND NOT A COUNT. `docs/manual/dev/scenes.md` says which scenes can be
rendered from props alone today. A document is a photograph: it was true when it
was written and nothing tells you when it stops being true, and the thing that
makes it false — a page that starts fetching, a panel that starts reading the
route — is exactly the change nobody notices. So the set is frozen in
`frontend/scene-baseline.json` and checked instead of described.

THE RULE, in two halves:

  1. A scene that IS standalone-ready today may not stop being. The frozen set
     may only grow.
  2. A scene that is NEW — not in the scene set this baseline knows — must be
     standalone-ready from its first commit. New pages and new panels are the
     only place a ratchet can be a rule instead of a description: the debt that
     is already here is grandfathered in the `debt` list and may sit there, but
     nothing new may join it.

  Debt is therefore a list, not a count: it is what makes "new" decidable. A
  scene is new when it is in neither list, and it is pre-existing debt when it
  is in `debt` — which is also why `--update` may add to `ready` but never to
  `debt`, and may drop from `debt` but never from `ready`.

WHAT A SCENE IS. Two kinds, both taken from the tree rather than from a list:

  * A PAGE is a `.vue` under `frontend/src/views/` that the router reaches.
    The router's import graph is walked from every `frontend/src/router/**/*.ts`
    (spec files excluded), through every `.ts` module it reaches, and any `.vue`
    under `views/` on the way is a page. A new page therefore needs no edit here
    to be judged — registering it in the router is what makes it a scene.
  * A PANEL is every `.vue` under `frontend/src/components/panels/`. There is no
    registry to keep in step; the directory is the set.

STANDALONE-READY means grade A from `.claude/scripts/frontend_grade.py` — the
same function `arch-metrics.py` reports (so the board and the gate cannot
disagree about a scene). The grade is a regex estimate, not a compiler, and its
blind spots are documented there.

WHAT A FAILURE LOOKS LIKE, and what to do about it: `docs/manual/dev/scenes.md`
has the rule in Chinese and the recipe for pulling a fetch or a route read out
of a scene; the short version is that data comes in as props and intent goes out
as an event, with the fetch left in a composable the page calls.

THE CATALOG WARNING. A standalone-ready component that is not in the preview
site's registry (`frontend/src/views/demo/catalog.ts`) is provable but
unwatchable — nothing renders it, so the next person to break it finds out from
this check rather than from a screen. That is a warning count, never a failure:
a page is not a panel, and the catalog carries what somebody chose to show.
`pnpm exec vitest run src/views/demo/catalog.spec.ts` is what proves the
registered ones really mount.

Exit codes: 0 nothing to report, 1 a scene regressed or a new scene is not
standalone-ready, 2 could not judge (no `frontend/src/` under `--root`, no
readable baseline, an unreadable scene file). 2 is never a pass.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ratchet_report import as_json, cannot_judge, emit, verdict as json_verdict

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Where the frontend lives, and the two halves of the scene set.
SRC_DIR = "frontend/src"
ROUTER_DIR = "frontend/src/router"
VIEWS_DIR = "frontend/src/views/"
PANELS_DIR = "frontend/src/components/panels"

#: The registry the catalog warning counts against, and the `file:` field in it.
CATALOG = "frontend/src/views/demo/catalog.ts"
CATALOG_FILE = re.compile(r"""\bfile:\s*['"]([^'"]+)['"]""")

DEFAULT_BASELINE = "frontend/scene-baseline.json"

#: A spec file is not a scene and not a router module to walk from.
SPEC = re.compile(r"\.(spec|test)\.(ts|js|vue)$")


class Unjudgeable(Exception):
    """The tree or the baseline could not be read. Exit 2, never a pass."""


# --------------------------------------------------------------------- loading


def load_frontend_grade() -> Any:
    """The grader itself, from the one module that defines it.

    Loaded from THIS script's directory, never from `--root`: the rules a check
    judges by are the repository's, and a `--root` tree is only a tree to
    judge. A copy of the grade here would be a second answer to "does this run
    alone", and the day the two disagree is the day the gate passes something
    the board calls C.
    """
    source = Path(__file__).resolve().parent / "frontend_grade.py"
    spec = importlib.util.spec_from_file_location("_frontend_grade", source)
    if spec is None or spec.loader is None:  # pragma: no cover - unreadable script
        raise Unjudgeable(f"cannot load the grader at {source}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_frontend_grade"] = module
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ the scenes


def scene_paths(root: Path) -> list[str]:
    """Every scene, as a repo-relative path, sorted. Pages then panels.

    Pages come from the router's import graph rather than from a list of
    routes: a page is a `.vue` under `views/` that the router reaches, and a
    new route is picked up by existing. Panels are the directory.
    """
    src = root / "frontend" / "src"
    if not src.is_dir():
        raise Unjudgeable(f"no {SRC_DIR}/ under {root}")

    pages: set[str] = set()
    seen: set[Path] = set()
    stack = [p for p in sorted((src / "router").rglob("*.ts")) if p.is_file() and not SPEC.search(p.name)]
    if not stack:
        raise Unjudgeable(f"no router modules under {root / ROUTER_DIR}")

    grade_module = load_frontend_grade()
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Unjudgeable(f"cannot read {path}: {exc}") from exc
        for _, spec in grade_module.specifiers(text):
            resolved = grade_module.resolve_spec(spec, path, src)
            if resolved is None:
                continue
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            if resolved.suffix == ".vue":
                if rel.startswith(VIEWS_DIR):
                    pages.add(rel)
            elif resolved.suffix == ".ts":
                stack.append(resolved)  # a router module, or one that names pages

    panels = {
        p.relative_to(root).as_posix()
        for p in (src / "components" / "panels").rglob("*.vue")
        if p.is_file()
    }
    return sorted(pages) + sorted(panels)


def grade_scenes(root: Path) -> dict[str, Any]:
    """Grade every scene. Returns `{scene: Grade}` — one `api_reach` for all."""
    grade_module = load_frontend_grade()
    reach = grade_module.api_reach(root)
    grades = {}
    for rel in scene_paths(root):
        path = root / rel
        if not path.is_file():
            raise Unjudgeable(f"scene {rel} is not a file")
        try:
            grades[rel] = grade_module.grade_component(root, path, reach)
        except OSError as exc:
            raise Unjudgeable(f"cannot read {rel}: {exc}") from exc
    return grades


def catalog_entries(root: Path) -> set[str]:
    """The `file:` paths registered in the preview site's catalog.

    Unreadable or absent is an empty set, not an error: the catalog is a
    courtesy, and a warning nobody can compute is still not a failure.
    """
    path = root / CATALOG
    if not path.is_file():
        return set()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return {match for match in CATALOG_FILE.findall(text) if match.endswith(".vue")}


# ------------------------------------------------------------------ the ratchet


def key_of(scene: str) -> str:
    """`frontend/src/views/404.vue` -> `src/views/404.vue`.

    Baseline keys are relative to `frontend/`, like every other baseline in
    that directory (`import-boundary-baseline.json`): a path relative to the
    repository root only reads correctly from here.
    """
    if not scene.startswith("frontend/"):
        raise ValueError(f"not under frontend/: {scene}")
    return scene[len("frontend/") :]


def scene_of(key: str) -> str:
    """The inverse of `key_of`."""
    if not key.startswith("src/"):
        raise ValueError(f"not a src/ key: {key}")
    return f"frontend/{key}"


@dataclass(frozen=True)
class Baseline:
    """The frozen scene set: what is standalone-ready, and what is old debt."""

    ready: frozenset[str] = frozenset()
    debt: frozenset[str] = frozenset()

    @property
    def known(self) -> frozenset[str]:
        return self.ready | self.debt


@dataclass
class Verdict:
    """What the tree says about the baseline, before it decides the exit code."""

    #: in `ready` and no longer grade A: (key, now, reasons)
    regressions: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    #: not in the baseline at all and not grade A: (key, now, reasons)
    new_debt: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)
    #: (key, why) — a scene that got better, or a frozen one that is gone
    improvements: list[tuple[str, str]] = field(default_factory=list)
    #: standalone-ready scenes with no catalog entry (a warning, never a failure)
    uncatalogued: list[str] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)
    debt: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions and not self.new_debt


def judge(baseline: Baseline, grades: dict[str, Any], catalog: set[str]) -> Verdict:
    """Compare the tree against the baseline. Pure, so it is testable."""
    verdict = Verdict()
    for scene in sorted(grades):
        key = key_of(scene)
        grade = grades[scene]
        if grade.standalone:
            verdict.ready.append(key)
            if key not in baseline.ready:
                # got better than the baseline says: a scene that was debt, or
                # one the frozen set has never seen. Neither is a failure.
                verdict.improvements.append(
                    (key, "was debt, is standalone-ready now")
                    if key in baseline.debt
                    else (key, "is new and standalone-ready")
                )
            continue
        if key in baseline.ready:
            verdict.regressions.append((key, grade.letter, grade.reasons))
        elif key in baseline.debt:
            verdict.debt.append(key)
        else:
            verdict.new_debt.append((key, grade.letter, grade.reasons))

    for key in sorted(baseline.ready | baseline.debt):
        if scene_of(key) not in grades:
            verdict.improvements.append((key, "is no longer in the tree"))

    verdict.uncatalogued = sorted(key for key in verdict.ready if key not in catalog)
    return verdict


def tightened(
    baseline: Baseline, grades: dict[str, Any], *, bootstrap: bool = False
) -> tuple[Baseline, list[tuple[str, str]]]:
    """The baseline `--update` would write, and what it refused to do.

    Adding a scene to `ready` is what "the set may only grow" means, and
    dropping one from `debt` pays debt down. The two refusals are the ratchet:
    a scene that regressed is NOT dropped from `ready` (that would launder the
    regression into an edit), and a scene that is not standalone-ready is NOT
    added to `debt` (that would grandfather today's mistake as tomorrow's
    precedent). Both come back as refusals and the caller exits 1.

    `bootstrap` is the one exception, and it is a file that does not exist yet:
    with no baseline at all there is nothing to grandfather — every scene in
    the tree is by definition pre-existing, which is what the first frozen set
    says. Deleting the baseline to launder a regression is not available: the
    check cannot judge without it (exit 2), and the deletion is in the diff.

    A frozen scene that is gone from the tree is dropped from both lists: the
    path no longer exists, so it can neither pass nor fail, and a baseline that
    keeps it grows a list of paths nobody can act on.
    """
    ready = set(baseline.ready)
    debt = set(baseline.debt)
    refusals: list[tuple[str, str]] = []

    for scene in sorted(grades):
        key = key_of(scene)
        grade = grades[scene]
        if grade.standalone:
            ready.add(key)
            debt.discard(key)
        elif key in baseline.ready:
            refusals.append((key, f"stopped being standalone-ready (now {grade.letter})"))
        elif key not in baseline.debt and not bootstrap:
            refusals.append((key, f"is new and not standalone-ready (grade {grade.letter})"))
        else:
            debt.add(key)

    for key in set(ready) | set(debt):
        if scene_of(key) not in grades:
            ready.discard(key)
            debt.discard(key)

    return Baseline(ready=frozenset(ready), debt=frozenset(debt)), refusals


# ------------------------------------------------------------------- the I/O


def read_baseline(path: Path, *, create_if_missing: bool) -> Baseline:
    """Parse the baseline. Missing is an error unless we are writing one.

    "Writing one" is the initial freeze: there is no frozen set yet, so the
    first `--update` writes the tree as it stands. A path that is *there* and
    unreadable is never a bootstrap — that would turn a corrupt baseline into a
    licence to rewrite it.
    """
    if not path.is_file():
        if create_if_missing:
            return Baseline()
        raise Unjudgeable(
            f"no baseline at {path} — this check cannot tell a new scene from "
            f"an old one without it (run with --update to write the first one)"
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Unjudgeable(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise Unjudgeable(f"{path} is not a JSON object")
    ready, debt = data.get("ready"), data.get("debt")
    if not isinstance(ready, list) or not isinstance(debt, list):
        raise Unjudgeable(f'{path} needs a "ready" and a "debt" list')
    for key in [*ready, *debt]:
        if not isinstance(key, str) or not key.startswith("src/"):
            raise Unjudgeable(f"{path} has a key that is not a frontend-relative scene: {key!r}")
    return Baseline(ready=frozenset(ready), debt=frozenset(debt))


def write_baseline(path: Path, baseline: Baseline) -> None:
    """`_comment` first, so the rule travels with the file it governs."""
    payload = {
        "_comment": (
            "Scenes that already run standalone, frozen by "
            ".claude/scripts/scene-ratchet.py. `ready` may only grow: a scene in "
            "it that stops being grade A fails the check. `debt` is the "
            "pre-existing scenes that are not standalone-ready — it may only "
            "shrink, and a scene in neither list is NEW and must be grade A. "
            "Regenerate with `pnpm run lint:scenes:update` (it never accepts a "
            "regression). What a scene is and what grade A means: "
            "docs/manual/dev/scenes.md."
        ),
        "ready": sorted(baseline.ready),
        "debt": sorted(baseline.debt),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def format_report(verdict: Verdict, baseline: Baseline) -> str:
    """The human half: what is wrong, why, and the one command that fixes it."""
    lines: list[str] = []
    if verdict.new_debt:
        lines.append("New scenes that are not standalone-ready (this is what the ratchet blocks):")
        for key, letter, reasons in verdict.new_debt:
            lines.append(f"  {key}: {letter}")
            lines.extend(f"    - {reason}" for reason in reasons)
        lines.append("")
        lines.append("A scene added from today on must render from props and emits alone:")
        lines.append("no API layer, no route, no business store. Fetch in a composable the")
        lines.append("page calls and pass the result down. How, in Chinese, with the recipe")
        lines.append("for a page and a panel: docs/manual/dev/scenes.md.")
        lines.append("")
    if verdict.regressions:
        lines.append("Scenes that stopped being standalone-ready (also what the ratchet blocks):")
        for key, letter, reasons in verdict.regressions:
            lines.append(f"  {key}: A -> {letter}")
            lines.extend(f"    - {reason}" for reason in reasons)
        lines.append("")
        lines.append("These are frozen in frontend/scene-baseline.json because they ran")
        lines.append("standalone once. Putting the reach-through back is not an option the")
        lines.append("check has: fix the scene, or ask for the rule to be changed in review.")
        lines.append("")
    if verdict.improvements:
        lines.append("Better than the baseline says — tighten it:")
        for key, why in verdict.improvements:
            lines.append(f"  {key}: {why}")
        lines.append("")
        lines.append("Run: pnpm run lint:scenes:update  (then commit frontend/scene-baseline.json)")
        lines.append("")
    if verdict.uncatalogued:
        lines.append(
            f"warning: {len(verdict.uncatalogued)} standalone-ready scene(s) have no "
            "/demo/catalog entry (not a failure):"
        )
        lines.extend(f"  {key}" for key in verdict.uncatalogued)
        lines.append("")
    lines.append(
        f"{len(verdict.ready)} standalone-ready, {len(verdict.debt)} pre-existing debt, "
        f"baseline has {len(baseline.ready)} ready and {len(baseline.debt)} debt"
    )
    if not verdict.ok:
        lines.append("")
        lines.append("A ratchet that can be satisfied by editing its baseline is not a ratchet:")
        lines.append("--update only ever adds to `ready` and drops from `debt`.")
    return "\n".join(lines)


def debt_details(grades: dict[str, Any]) -> list[dict[str, Any]]:
    """One row per scene that is not standalone-ready, for the JSON record.

    The scenes that are fine are not listed: the record is expanded to find out
    what to work on, and 128 rows of "grade A" would bury the ten that are not.
    """
    return [
        {
            "file": key_of(scene),
            "grade": grades[scene].letter,
            "reasons": list(grades[scene].reasons),
        }
        for scene in sorted(grades)
        if not grades[scene].standalone
    ]


def run(root: Path, baseline_path: Path, *, update: bool, listing: bool) -> int:
    """Judge the tree at `root` against `baseline_path` and print the answer."""
    try:
        grades = grade_scenes(root)
        bootstrap = update and not baseline_path.is_file()
        baseline = read_baseline(baseline_path, create_if_missing=update)
    except Unjudgeable as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        cannot_judge("scene-ratchet", exc)

    catalog = catalog_entries(root)

    if update:
        next_baseline, refusals = tightened(baseline, grades, bootstrap=bootstrap)
        if refusals:
            print("refusing to update: the baseline may only grow and only shrink debt", file=sys.stderr)
            for key, why in refusals:
                print(f"  {key}: {why}", file=sys.stderr)
            print(
                "\nFix the scene (docs/manual/dev/scenes.md), or leave the baseline alone.",
                file=sys.stderr,
            )
            return 1
        write_baseline(baseline_path, next_baseline)
        print(
            f"baseline updated: {len(next_baseline.ready)} standalone-ready, "
            f"{len(next_baseline.debt)} debt — {baseline_path}"
        )
        return 0

    verdict = judge(baseline, grades, catalog)
    if as_json():
        # The unit here is a scene, and a scene costs at most one: `frozen` is
        # 1 for an allowance the baseline carries and `actual` is 0 once the
        # scene no longer needs it. A scene that got better without ever being
        # frozen (a brand new standalone-ready one) is not a stale allowance and
        # is left out — there is nothing on the books to clear.
        emit(
            json_verdict(
                check_id="scene-ratchet",
                ok=verdict.ok,
                actual=len(verdict.debt) + len(verdict.regressions) + len(verdict.new_debt),
                frozen=len(baseline.debt),
                stale=[
                    {"file": key, "frozen": 1, "actual": 0, "why": why}
                    for key, why in verdict.improvements
                    if key in baseline.ready or key in baseline.debt
                ],
                details=debt_details(grades),
            )
        )
        return 0 if verdict.ok else 1

    if listing:
        for scene in sorted(grades):
            print(f"{grades[scene].letter} {key_of(scene)}")
            if not grades[scene].standalone:
                for reason in grades[scene].reasons:
                    print(f"    - {reason}")
        print()
    print(format_report(verdict, baseline))
    return 0 if verdict.ok else 1


# ------------------------------------------------------------------ self-test

def _router(extra: str = "") -> str:
    """The fixture's router: three pages, plus whatever `extra` route lines add.

    Every case builds its router through this, so a case that adds a page does
    not silently drop the other three from the scene set — which would read as
    "the scene is gone" and hide what the case is actually about.
    """
    routes = "\n".join(
        f"  {{ name: '{name}', path: '{path}', component: () => import('{component}') }},"
        for name, path, component in (
            ("home", "/", "@/views/Home.vue"),
            ("heavy", "/heavy", "@/views/Heavy.vue"),
            ("typed", "/typed", "@/views/Typed.vue"),
        )
    )
    return (
        "import type { RouteRecordRaw } from 'vue-router'\n"
        "import { pages } from './pages'\n"
        "export const routes: RouteRecordRaw[] = [\n"
        f"{routes}\n{extra}  ...pages,\n"
        "]\n"
    )


#: The fixture tree the self-test judges. Small on purpose: one page per grade,
#: one panel per grade, and the two shapes this check exists to tell apart — a
#: scene that reaches the API layer through a chain, and one that only names a
#: type from it.
FIXTURE: dict[str, str] = {
    "frontend/src/api.ts": "export const api = { get: () => fetch('/x') }\n",
    "frontend/src/direct.ts": "import { api } from './api'\nexport const go = () => api.get()\n",
    "frontend/src/router/index.ts": _router(),
    # A router module that itself names a page: the walk must follow it.
    "frontend/src/router/pages.ts": (
        "export const pages = [\n"
        "  { name: 'nested', path: '/nested', component: () => import('@/views/Nested.vue') },\n"
        "]\n"
    ),
    "frontend/src/router/index.spec.ts": "import '@/views/NotAPage.vue'\n",
    "frontend/src/views/Home.vue": (
        '<script setup lang="ts">\ndefineProps<{ title: string }>()\n</script>\n'
        "<template><div>{{ title }}</div></template>\n"
    ),
    # Reaches the API layer through a chain, which is the axis a naive count misses.
    "frontend/src/views/Heavy.vue": (
        '<script setup lang="ts">\nimport { go } from \'@/direct\'\ngo()\n</script>\n'
        "<template><div /></template>\n"
    ),
    # Only names a type from the API layer: erased at build time, so still grade A.
    "frontend/src/views/Typed.vue": (
        "<script setup lang=\"ts\">\nimport type { Api } from '@/api'\n"
        "defineProps<{ thing: Api }>()\n</script>\n<template><div /></template>\n"
    ),
    "frontend/src/views/Nested.vue": (
        "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
        "<template><div>{{ n }}</div></template>\n"
    ),
    "frontend/src/views/NotAPage.vue": (
        "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
        "<template><div>{{ n }}</div></template>\n"
    ),
    "frontend/src/components/panels/Plain.vue": (
        "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
        "<template><div>{{ n }}</div></template>\n"
    ),
    "frontend/src/components/panels/Wired.vue": (
        "<script setup lang=\"ts\">\nimport { useSpaceStore } from '@/stores/space'\n"
        "const space = useSpaceStore()\n</script>\n<template><div>{{ space.id }}</div></template>\n"
    ),
    "frontend/src/views/demo/catalog.ts": (
        "export const CATALOG = [\n"
        "  { id: 'plain', file: 'src/components/panels/Plain.vue' },\n"
        "]\n"
    ),
}

#: The baseline the fixture's own tree deserves: `ready` is what is grade A
#: today, `debt` is what is not. Written out rather than derived, because a
#: self-test that recomputes the answer can only agree with itself.
FIXTURE_BASELINE = {
    "ready": [
        "src/components/panels/Plain.vue",
        "src/views/Home.vue",
        "src/views/Nested.vue",
        "src/views/Typed.vue",
    ],
    "debt": ["src/components/panels/Wired.vue", "src/views/Heavy.vue"],
}


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


def _fixture_baseline(root: Path, changes: dict[str, list[str]] | None = None) -> Path:
    """The baseline file for `root`'s fixture, with `changes` applied."""
    baseline = {kind: list(keys) for kind, keys in FIXTURE_BASELINE.items()}
    baseline.update(changes or {})
    path = root / "frontend" / "scene-baseline.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")
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
        # A crash is never a result. Without this, an exception inside the CLI
        # exits 1 and satisfies every "this must fail" case below for a reason
        # that has nothing to do with the rule being tested.
        if "Traceback" in result.stderr:
            failures.append(f"crashed: {' '.join(args) or 'check'}\n{result.stderr.strip()}")
        return result

    with tempfile.TemporaryDirectory(prefix="scene-ratchet-selftest-") as raw:
        root = Path(raw)

        # -- the fixture is the tree the baseline describes -----------------
        _fixture(root)
        baseline_path = _fixture_baseline(root)
        result = run_cli(root, baseline_path)
        check("the fixture's own baseline passes", result.returncode, 0)
        check("grades the pages the router reaches", "A src/views/Home.vue" in run_cli(
            root, baseline_path, "--list").stdout, True)
        check("does not judge a spec file's imports", "src/views/NotAPage.vue" in result.stdout, False)

        # -- 1. a baselined scene that regresses -----------------------------
        _fixture(root, {"frontend/src/views/Home.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        result = run_cli(root, baseline_path)
        check("a baselined scene that regresses fails", result.returncode, 1)
        check("and is named with its old grade", "src/views/Home.vue: A -> C" in result.stdout, True)
        check("and says why", "imports the API layer" in result.stdout, True)

        # -- 2. a new page that is not standalone-ready ----------------------
        brand = "  { name: 'brand', path: '/brand', component: () => import('@/views/Brand.vue') },\n"
        _fixture(root)
        _fixture(root, {
            "frontend/src/views/Brand.vue": (
                '<script setup lang="ts">\nimport { useRoute } from \'vue-router\'\n'
                "const route = useRoute()\n</script>\n"
                "<template><div>{{ route.path }}</div></template>\n"
            ),
            "frontend/src/router/index.ts": _router(brand),
        })
        result = run_cli(root, baseline_path)
        check("a new page that is not standalone-ready fails", result.returncode, 1)
        check("and is reported as new", "src/views/Brand.vue: D" in result.stdout, True)
        check("and says why", "reads the route" in result.stdout, True)
        # The whole point of the two lists: debt is what --update may pay down,
        # and a new scene is not debt, so nothing offers to freeze it.
        check("and offers no baseline edit for it", "pnpm run lint:scenes:update" in result.stdout, False)

        # -- 3. a new page that IS standalone-ready --------------------------
        _fixture(root)
        _fixture(root, {
            "frontend/src/views/Brand.vue": (
                "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
                "<template><div>{{ n }}</div></template>\n"
            ),
            "frontend/src/router/index.ts": _router(brand),
        })
        result = run_cli(root, baseline_path)
        check("a new page that is standalone-ready passes", result.returncode, 0)
        check("and is offered to the baseline", "src/views/Brand.vue: is new and standalone-ready" in result.stdout, True)

        # -- 4. pre-existing debt --------------------------------------------
        _fixture(root)
        result = run_cli(root, baseline_path)
        check("pre-existing debt passes", result.returncode, 0)
        check("and is not offered as an improvement", "Heavy.vue: was debt" in result.stdout, False)

        # -- 5. a type-only import is not reach ------------------------------
        #    The control: the same shape as a VALUE import is graded C, and the
        #    type-only one is graded A. Without the control this case would pass
        #    for a grader that never looked at Typed.vue at all.
        _fixture(root)
        result = run_cli(root, baseline_path, "--list")
        check("a type-only import is not reach", "A src/views/Typed.vue" in result.stdout, True)
        check("the same import as a value is reach", "C src/views/Heavy.vue" in result.stdout, True)
        _fixture(root, {"frontend/src/views/Typed.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        result = run_cli(root, baseline_path)
        check("so a plain import of the same module still fails", result.returncode, 1)
        check("for the same scene the type-only one passed", "src/views/Typed.vue: A -> C" in result.stdout, True)

        # -- 7. the chain through a `.vue` edge ------------------------------
        #    The same rule as case 5, one edge further out: a page that renders
        #    a child component which fetches needs the network just as much as
        #    one that calls it itself. Only `.ts` edges were followed once, and
        #    this is what that cost: the page was graded A and frozen as ready.
        _fixture(root)
        wrapped = "  { name: 'wrapped', path: '/wrapped', component: () => import('@/views/Wrapped.vue') },\n"
        _fixture(root, {
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
            "frontend/src/views/Wrapped.vue": (
                '<script setup lang="ts">\nimport ChildFetch from \'@/components/ChildFetch.vue\'\n'
                "</script>\n<template><ChildFetch /></template>\n"
            ),
            "frontend/src/router/index.ts": _router(wrapped),
        })
        result = run_cli(root, baseline_path, "--list")
        check("the page that renders a fetching child is graded C",
              "C src/views/Wrapped.vue" in result.stdout, True)
        result = run_cli(root, baseline_path)
        check("so it is not frozen as ready", "src/views/Wrapped.vue: is new and standalone-ready" in result.stdout, False)

        # -- 6. cannot judge --------------------------------------------------
        _fixture(root)
        result = run_cli(root, baseline_path)
        check("a healthy tree is judged", result.returncode, 0)
        missing = root / "absent-baseline.json"
        check("a missing baseline cannot be judged", run_cli(root, missing).returncode, 2)
        broken = root / "broken-baseline.json"
        broken.write_text("{ not json", encoding="utf-8")
        check("an unreadable baseline cannot be judged", run_cli(root, broken).returncode, 2)
        wrong = root / "wrong-baseline.json"
        wrong.write_text('{"ready": ["views/Home.vue"], "debt": []}\n', encoding="utf-8")
        check("a key that is not frontend-relative cannot be judged", run_cli(root, wrong).returncode, 2)
        empty = Path(raw) / "empty-root"
        empty.mkdir()
        check("a tree with no frontend/src cannot be judged", run_cli(empty, baseline_path).returncode, 2)

        # -- 7. what --update refuses, and what it may do --------------------
        _fixture(root, {"frontend/src/views/Home.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        before = baseline_path.read_text(encoding="utf-8")
        result = run_cli(root, baseline_path, "--update")
        check("--update refuses to launder a regression", result.returncode, 1)
        check("and leaves the baseline alone", baseline_path.read_text(encoding="utf-8"), before)

        _fixture(root)
        _fixture(root, {
            "frontend/src/views/Brand.vue": (
                '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
                "<template><div /></template>\n"
            ),
            "frontend/src/views/Home.vue": "",
            "frontend/src/router/index.ts": _router(
                "  { name: 'brand', path: '/brand', component: () => import('@/views/Brand.vue') },\n"
            ),
        })
        result = run_cli(root, baseline_path, "--update")
        check("--update refuses to grandfather a new scene", result.returncode, 1)
        check("and names it", "src/views/Brand.vue" in result.stderr, True)

        # Debt that got paid down is dropped; a ready scene that is gone is
        # dropped too, and neither is a refusal.
        _fixture(root, {
            "frontend/src/views/Heavy.vue": (
                "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
                "<template><div>{{ n }}</div></template>\n"
            ),
            "frontend/src/views/Nested.vue": "",
            "frontend/src/router/pages.ts": "export const pages = []\n",
        })
        result = run_cli(root, baseline_path, "--update")
        check("--update pays debt down", result.returncode, 0)
        written = json.loads(baseline_path.read_text(encoding="utf-8"))
        check("the paid-down scene is ready now", "src/views/Heavy.vue" in written["ready"], True)
        check("and no longer debt", "src/views/Heavy.vue" in written["debt"], False)
        check("a scene gone from the tree is dropped", "src/views/Nested.vue" in written["ready"], False)
        check("and the updated baseline passes", run_cli(root, baseline_path).returncode, 0)

        # -- 8. the committed baseline describes the tree it was written on ---
        #    Not a re-run of the check (that is the CI step): a baseline keyed by
        #    absolute paths, or one listing a scene that is not a scene, would
        #    otherwise only show up as a check that passes over nothing.
        try:
            committed = read_baseline(REPO_ROOT / DEFAULT_BASELINE, create_if_missing=False)
            grades = grade_scenes(REPO_ROOT)
        except Unjudgeable as exc:
            failures.append(f"the committed baseline is not usable: {exc}")
        else:
            unknown = committed.known - {key_of(scene) for scene in grades}
            check("every committed baseline key is a scene in the tree", sorted(unknown), [])
            check("ready and debt do not overlap", sorted(committed.ready & committed.debt), [])

            # The wiring, which is what makes all of the above a gate: a check
            # nothing runs is a document with extra steps.
            package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
            script = package["scripts"].get("lint:scenes", "")
            check("frontend/package.json runs this script", script.endswith("scene-ratchet.py"), True)
            workflow = (REPO_ROOT / ".github" / "workflows" / "frontend.yml").read_text(encoding="utf-8")
            check("and CI runs that script", "run: pnpm run lint:scenes" in workflow, True)
            hooks = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
            check("and a commit runs it too", "scene-ratchet" in hooks, True)

    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print(f"  {line}")
        return 1
    print(
        "PASS: scene-ratchet self-test (a regressed scene, a new scene that is not "
        "ready, debt that is grandfathered, debt paid down, a type-only import that "
        "is not reach, --update refusing both edits, and four ways of not being able "
        "to judge)"
    )
    return 0


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to judge")
    parser.add_argument("--baseline", default=None, help=f"default: {DEFAULT_BASELINE}")
    parser.add_argument("--update", action="store_true", help="add ready scenes, drop paid debt")
    parser.add_argument("--list", action="store_true", help="print every scene with its grade")
    parser.add_argument(
        "--json",
        action="store_true",
        help="print one JSON record instead of the report (the collector reads it)",
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    baseline_path = Path(args.baseline) if args.baseline else root / DEFAULT_BASELINE
    return run(root, baseline_path, update=args.update, listing=args.list)


if __name__ == "__main__":
    sys.exit(main())
