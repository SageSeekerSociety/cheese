#!/usr/bin/env python3
"""frontend_grade.py — grade one frontend component A/B/C/D, in one place.

WHY A MODULE AND NOT TWO COPIES. The grade has two callers with opposite jobs:
`arch-metrics.py` reports it (a board, never red) and `scene-ratchet.py` gates
on it (a ratchet, exit 1). Two implementations of "standalone-ready" would
drift, and the day they drift is the day the gate passes a scene the board says
is C — so the method lives here and both load it. `arch-metrics.py` already
imports its file-size caps the same way, from the gate that enforces them.

WHAT IT MEASURES. A component is graded by what it needs at RENDER time, which
is the question "can this be mounted on its own, without a backend, a route or
an Apollo-shaped store":

  A  nothing but props and emits. Standalone-ready: the only thing missing is a
     fixture in the shape its props describe.
  B  it reads an app-chrome store (`usePageTitleStore`, `useNavigationStore`) —
     stores the shell owns, not the data a page is about.
  C  it reaches the network: it imports `@/api`, `@/network/*` or
     `@/services/*`, OR any module that transitively reaches one of those, OR
     it calls `fetch`/`axios` itself — or it reads a business store.
  D  it is tied to where it is mounted, or to a channel other than
     props/emits: `useRoute`/`useRouter`/`$router`/`vue-router`,
     `$parent`/`$root`, an event bus, or `provide`/`inject`.

  The first letter that applies, worst first, is the grade: a component that
  reads the route AND fetches is D, not C.

THE TYPE-ONLY EDGE, AND WHY IT IS NOT AN EDGE. `import type` is erased at build
time, so it pulls in no runtime dependency and no component became
un-standalone by declaring one — a component that only does
`import type { PreviewSession } from '../api'` has no fetching code in it at
all. Value imports skip type-only specifiers, `api_reach` skips them too: an
edge that exists only as a type never enters the transitive closure. (#2174's
inventory counted them, which is how `PanelPreviewView` was called C while
containing no fetch of its own; fixing it in this one place fixed both the
board and the gate at once.)

WHAT IT COSTS, stated so a grade is read as an estimate and not a verdict:

  - Regex over the `<script>` block and the template, not a compiler (the
    frontend audit's own method). `defineProps`/`defineEmits` are not counted:
    no grade uses them.
  - A specifier this parser cannot resolve (a package, a path it cannot see
    through) is treated as external rather than as an edge. A component that
    reaches the API through a chain the regex misses is graded one letter too
    high; a `reach` computed over `.ts` and `.vue` only has the same blind
    spot. `@/` is resolved against `frontend/src`, matching `vite.config.ts`.
  - A store is recognised by the `useXStore` naming convention. One spelled
    another way is invisible here.

`reasons` is the other half of the answer and exists for the gate: a failure
that says "PanelCard is C" is a puzzle, and one that says "reaches the API
layer through components/room/composables/useRoomSocket.ts" is a task.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: The grade a scene must have to be called standalone-ready.
STANDALONE = "A"

VUE_IMPORT = re.compile(r"""import\s+(type\s+)?(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""", re.S)
VUE_DYN_IMPORT = re.compile(r"""import\(\s*['"]([^'"]+)['"]\s*\)""")
SCRIPT_BLOCK = re.compile(r"<script[^>]*>(.*?)</script>", re.S)
TEMPLATE_BLOCK = re.compile(r"<template[^>]*>(.*?)</template>", re.S)
STORE_USE = re.compile(r"\buse([A-Za-z0-9_]+)Store\b")
ROUTER_USE = re.compile(r"\buseRoute\s*\(|\buseRouter\s*\(|\$router\b")
PARENT_USE = re.compile(r"\$parent|\$root")
BUS_USE = re.compile(r"\beventBus\b|\$eventBus\b")
INJECT_USE = re.compile(r"\binject\s*\(")
PROVIDE_USE = re.compile(r"\bprovide\s*\(")
RAW_HTTP = re.compile(r"\bfetch\s*\(|\baxios\b")

#: Stores whose identity is app chrome rather than business data (the audit's list).
BENIGN_STORES = {"pageTitle", "navigation"}
#: `usePageTitleStore` -> `pageTitle`; the audit also folded `Title` into it.
STORE_ALIASES = {"Title": "pageTitle", "PageTitle": "pageTitle"}

#: The API surface: the fetch stack, the axios stack, and the service wrappers.
#: `utils/apiBase.ts` is excluded — it is the base URL, not a call.
API_EXCLUDED = ("frontend/src/utils/apiBase.ts",)


def frontend_files(root: Path, suffix: str) -> list[Path]:
    """Every `<suffix>` file under `frontend/src`, sorted."""
    src = root / "frontend" / "src"
    if not src.is_dir():
        return []
    return sorted(p for p in src.rglob(f"*{suffix}") if p.is_file())


def is_api_root(rel: str) -> bool:
    """Is this module part of the API surface? `rel` is repo-relative."""
    if rel in API_EXCLUDED:
        return False
    return (
        rel in ("frontend/src/api.ts", "frontend/src/network/index.ts")
        or rel.startswith("frontend/src/network/api/")
        or rel.startswith("frontend/src/services/")
    )


def resolve_spec(spec: str, importer: Path, src: Path) -> Path | None:
    """Resolve an import specifier to a file under `src`, or None if external."""
    if spec.startswith("@/"):
        base = src / spec[2:]
    elif spec.startswith("."):
        base = importer.parent / spec
    else:
        return None  # a package: not part of this graph
    candidates = [base, *(Path(f"{base}{ext}") for ext in (".ts", ".vue", ".js"))]
    candidates += [base / "index.ts", base / "index.vue"]
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def specifiers(text: str) -> list[tuple[bool, str]]:
    """Every import specifier in `text`, as `(is_type_only, specifier)`."""
    out = [(bool(kind), spec) for kind, spec in VUE_IMPORT.findall(text)]
    out += [(False, spec) for spec in VUE_DYN_IMPORT.findall(text)]
    return out


def api_reach(root: Path) -> set[Path]:
    """Modules under `frontend/src` whose import graph reaches an API root.

    Direct reach is a value import of an API root or a bare `fetch`/`axios`;
    the rest is closed transitively over `.ts` and `.vue` edges. A type-only
    import is not an edge: it is erased at build time, so a module that only
    declares a type from the API layer is not a module that reaches it.
    """
    src = root / "frontend" / "src"
    graph: dict[Path, set[Path]] = {}
    reach: set[Path] = set()
    for path in sorted(list(src.rglob("*.ts")) + list(src.rglob("*.vue"))):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        deps: set[Path] = set()
        direct = bool(RAW_HTTP.search(text))
        for is_type, spec in specifiers(text):
            if is_type:
                continue
            resolved = resolve_spec(spec, path, src)
            if resolved is None:
                continue
            try:
                rel = resolved.relative_to(root).as_posix()
            except ValueError:
                continue
            if is_api_root(rel):
                direct = True
            if resolved.suffix in (".ts", ".vue"):
                deps.add(resolved)
        graph[path.resolve()] = deps
        if direct:
            reach.add(path.resolve())
    changed = True
    while changed:  # transitive closure; the graph has cycles, so no topo order
        changed = False
        for node, deps in graph.items():
            if node in reach:
                continue
            if deps & reach:
                reach.add(node)
                changed = True
    return reach


@dataclass(frozen=True)
class Grade:
    """One component's grade and the reasons it was not handed a better one."""

    letter: str
    reasons: tuple[str, ...] = field(default=())

    @property
    def standalone(self) -> bool:
        return self.letter == STANDALONE


def normalise_store(name: str) -> str:
    """`useSpaceStore` -> `space`; `usePageTitleStore` -> `pageTitle`."""
    return STORE_ALIASES.get(name, name[:1].lower() + name[1:])


def grade_component(root: Path, path: Path, reach: set[Path] | None = None) -> Grade:
    """Grade the component at `path` (a `.vue` or a `.ts` file under src).

    `reach` is `api_reach(root)` — passed in when grading many files, because
    building it reads the whole tree and grading one file should not.
    """
    src = root / "frontend" / "src"
    if reach is None:
        reach = api_reach(root)
    text = path.read_text(encoding="utf-8", errors="replace")
    script = "\n".join(SCRIPT_BLOCK.findall(text))
    template = "\n".join(TEMPLATE_BLOCK.findall(text))
    whole = script + "\n" + template

    reasons: list[str] = []
    api_direct = bool(RAW_HTTP.search(script))
    if api_direct:
        reasons.append("calls fetch()/axios itself")
    for spec in specifiers(text):
        is_type, specifier = spec
        if is_type:
            continue  # a type-only import pulls in no runtime dependency
        resolved = resolve_spec(specifier, path, src)
        if resolved is None:
            continue
        try:
            rel = resolved.relative_to(root).as_posix()
        except ValueError:
            continue
        if is_api_root(rel):
            api_direct = True
            reasons.append(f"imports the API layer ({rel})")
        elif (
            resolved in reach
            and not rel.startswith("frontend/src/stores/")
            and resolved.suffix == ".ts"
        ):
            api_direct = True  # reaches the network through a chain
            reasons.append(f"reaches the API layer through {rel}")

    stores = {normalise_store(name) for name in STORE_USE.findall(whole)}
    hard = bool(
        ROUTER_USE.search(whole)
        or "vue-router" in script
        or PARENT_USE.search(text)
        or BUS_USE.search(text)
        or INJECT_USE.search(script)
        or PROVIDE_USE.search(script)
    )
    if ROUTER_USE.search(whole) or "vue-router" in script:
        reasons.append("reads the route (useRoute/useRouter/$router/vue-router)")
    if PARENT_USE.search(text):
        reasons.append("reads $parent/$root")
    if BUS_USE.search(text):
        reasons.append("uses an event bus")
    if INJECT_USE.search(script) or PROVIDE_USE.search(script):
        reasons.append("uses provide()/inject()")

    business = stores - BENIGN_STORES
    for name in sorted(business):
        reasons.append(f"reads the {name} store")

    if hard:
        grade = "D"
    elif api_direct or business:
        grade = "C"
    elif stores:
        grade = "B"
    else:
        grade = "A"
    return Grade(letter=grade, reasons=tuple(reasons))


def grade_frontend(root: Path) -> dict[str, Any]:
    """Count `.vue` components and grade each one A/B/C/D (the board's number)."""
    src = root / "frontend" / "src"
    reach = api_reach(root)
    grades: Counter[str] = Counter()
    lines_by_grade: Counter[str] = Counter()
    total_lines = 0
    for path in frontend_files(root, ".vue"):
        text = path.read_text(encoding="utf-8", errors="replace")
        lines = text.count("\n") + 1
        grade = grade_component(root, path, reach).letter
        grades[grade] += 1
        lines_by_grade[grade] += lines
        total_lines += lines

    n = sum(grades.values())
    return {
        "components": n,
        "lines": total_lines,
        "grades": {g: grades.get(g, 0) for g in "ABCD"},
        "grade_pct": {
            g: round(100 * grades.get(g, 0) / n, 1) if n else None for g in "ABCD"
        },
        "grade_lines": {g: lines_by_grade.get(g, 0) for g in "ABCD"},
    }
