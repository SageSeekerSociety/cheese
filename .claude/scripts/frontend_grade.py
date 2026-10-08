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
     `<router-view>`/`<router-link>`, `$parent`/`$root`, an event bus, or
     `provide`/`inject`.

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

The router import is the one place type-only specifiers still count, and it
does so on purpose: `docs/manual/dev/frontend.md` bans
`import type { RouteLocationRaw } from 'vue-router'` under
`frontend/src/components/**` too, and points at `@/lib/navTarget` as the
substitute. So `imports_router` reads the specifier list without dropping
them.

WHAT IT COSTS, stated so a grade is read as an estimate and not a verdict:

  - Regex over the `<script>` block and the template, not a compiler (the
    frontend audit's own method). `defineProps`/`defineEmits` are not counted:
    no grade uses them.
  - A specifier this parser cannot resolve (a package, a path it cannot see
    through) is treated as external rather than as an edge. A component that
    reaches the API through a chain the regex misses is graded one letter too
    high; a `reach` computed over `.ts` and `.vue` only has the same blind
    spot. `@/` is resolved against `frontend/src`, matching `vite.config.ts`.
    Both spellings of an edge are read — `import` and `export … from` — and
    both are regexes over the whole file, so a specifier-shaped string in a
    comment, a template or a string literal counts as an edge too. An edge
    that is really spelled some third way is still missed.
  - A store is recognised by the `useXStore` naming convention. One spelled
    another way is invisible here.
  - Comments are stripped before the router test: a file that mentions
    `vue-router` in a comment — `common/NavLink.vue` explains in its header
    why it does *not* import it — is not a file that depends on the router.
    The template half is read tag by tag (`<router-view>`, `<router-link>`),
    the way `scene-ratchet.py` pairs a page with its view.

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
#: `export … from '…'`, the other spelling of an edge. The clause between
#: `export` and `from` is captured whole because a braces list decides its own
#: type-onlyness specifier by specifier.
VUE_EXPORT_FROM = re.compile(
    r"""export\s+(type\s+)?(\*|\*\s+as\s+[\w$]+|\{[^}]*\})\s*from\s*['"]([^'"]+)['"]""",
    re.S,
)
#: One entry of an `export { … } from` list, if it is the type-only kind.
_TYPE_SPECIFIER = re.compile(r"\s*type\s+\S")
VUE_DYN_IMPORT = re.compile(r"""import\(\s*['"]([^'"]+)['"]\s*\)""")
SCRIPT_BLOCK = re.compile(r"<script[^>]*>(.*?)</script>", re.S)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_START = re.compile(r"</?\s*([A-Za-z][A-Za-z0-9_-]*)")
STORE_USE = re.compile(r"\buse([A-Za-z0-9_]+)Store\b")
ROUTER_USE = re.compile(r"\buseRoute\s*\(|\buseRouter\s*\(|\$router\b")
#: vue-router's two globals as a template tag, normalised (lowercased, dashes
#: dropped): `<router-view>`, `<RouterView>` and `<routerView>` are one name.
ROUTER_TAGS = {"routerview", "routerlink"}
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


def repo_src(root: Path) -> Path:
    """The `frontend/src` directory of a repo root — or no grading at all.

    A caller that hands the grader a directory that is not the repo root must
    not get a green answer: with a missing `frontend/src` the import graph is
    empty, every component looks standalone, and a gate would pass everything.
    So this raises instead of returning an empty tree. The CLI turns the
    LookupError into exit 2 — "cannot judge" — which is the only way a wrong
    root is allowed to fail loud."""
    src = root / "frontend" / "src"
    if not src.is_dir():
        raise LookupError(f"not a repo root (frontend/src missing): {root}")
    return src


def frontend_files(root: Path, suffix: str) -> list[Path]:
    """Every `<suffix>` file under `frontend/src`, sorted."""
    src = repo_src(root)
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
    """Every edge in `text`, as `(is_type_only, specifier)`.

    Imports and re-exports both: `export { x } from './x'` and `export * from
    './x'` pull the target into the module's runtime graph exactly as an import
    does, which is how an index barrel carries a chain. Reading only `import`
    made the barrel's edge invisible, and a file that fetched through one was
    graded A — the same shape of miss as the router test's substring match.
    """
    out = [(bool(kind), spec) for kind, spec in VUE_IMPORT.findall(text)]
    out += [
        (_export_is_type_only(kind, clause), spec)
        for kind, clause, spec in VUE_EXPORT_FROM.findall(text)
    ]
    out += [(False, spec) for spec in VUE_DYN_IMPORT.findall(text)]
    return out


def _export_is_type_only(leading_type: str | None, clause: str) -> bool:
    """Is this `export … from` erased at build time?

    `export type { A } from`, `export { type A } from` and `export type * from`
    are. `export *` and `export * as ns` are not — a module namespace is a
    runtime value — and a mixed list is not either: it is a runtime edge
    through the specifiers that are not marked `type`.
    """
    if leading_type:
        return True
    if not clause.startswith("{"):
        return False
    listed = [part for part in clause[1:-1].split(",") if part.strip()]
    return bool(listed) and all(_TYPE_SPECIFIER.match(part) for part in listed)


def api_reach(root: Path) -> set[Path]:
    """Modules under `frontend/src` whose import graph reaches an API root.

    Direct reach is a value import of an API root or a bare `fetch`/`axios`;
    the rest is closed transitively over `.ts` and `.vue` edges. An import and
    a re-export (`export { … } from`, `export * from`) are both edges — an
    index barrel that re-exports a module which fetches is how a chain gets
    past it — but a type-only one is not: it is erased at build time, so a
    module that only declares a type from the API layer does not reach it.

    `root` is resolved first. Every edge is a `resolve_spec` result, which is
    absolute, and an absolute path is never `relative_to` a relative root: with
    `Path('.')` every edge fell into the `ValueError` branch, the graph came
    out empty and every component graded A (356 became 486). Resolving here
    rather than refusing a relative root keeps every caller correct without
    each one having to remember.
    """
    root = root.resolve()
    src = repo_src(root)
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


def template_blocks(text: str) -> list[str]:
    """The contents of every top-level `<template>` block in an SFC.

    Three ways a naive `<template...>(.*?)</template>` read lies: a tag
    inside `<!-- ... -->` is not rendered (comments are stripped first); Vue
    templates nest (`<template v-if>`), so a non-greedy match ends at the
    first *inner* `</template>` and loses everything after it (nested tags
    are balanced here); and `title="</template>"` is an attribute value, not
    a tag — the scan honours quotes, so what an attribute says never becomes
    a tag boundary.
    """
    text = _HTML_COMMENT.sub("", text)
    blocks: list[str] = []
    i, n = 0, len(text)
    depth = 0
    block_start: int | None = None
    while i < n:
        if text[i] == "<":
            start = _TAG_START.match(text, i)
            if start is not None:
                # The end of this tag, honouring quoted attribute values:
                # `<` and `>` inside quotes are not tag boundaries.
                j = start.end()
                quote = ""
                while j < n:
                    char = text[j]
                    if quote:
                        if char == quote:
                            quote = ""
                    elif char in "\"'":
                        quote = char
                    elif char == ">":
                        break
                    j += 1
                if start.group(1).lower() == "template":
                    closing = text[i + 1] == "/"
                    if closing:
                        depth = max(0, depth - 1)
                        if depth == 0 and block_start is not None:
                            blocks.append(text[block_start:i])
                            block_start = None
                    elif text[j - 1] != "/":  # not self-closing
                        if depth == 0:
                            block_start = j + 1
                        depth += 1
                i = j + 1
                continue
        i += 1
    if block_start is not None:
        blocks.append(text[block_start:])  # an unclosed template: take the rest
    return blocks


def tag_names(block: str) -> list[str]:
    """Every real tag name in a template block, in order.

    Quote-aware like `template_blocks`: `title="<SettleView />"` is an
    attribute value, and the string it holds is not a render.
    """
    names: list[str] = []
    i, n = 0, len(block)
    while i < n:
        if block[i] == "<":
            start = _TAG_START.match(block, i)
            if start is not None:
                j = start.end()
                quote = ""
                while j < n:
                    char = block[j]
                    if quote:
                        if char == quote:
                            quote = ""
                    elif char in "\"'":
                        quote = char
                    elif char == ">":
                        break
                    j += 1
                names.append(start.group(1))
                i = j + 1
                continue
        i += 1
    return names


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")


def strip_comments(script: str) -> str:
    """`script` with its comments removed.

    Block comments go first: a `//` inside one is prose, and stripping lines
    first would leave the rest of the comment behind as if it were code.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", script))


def imports_router(script: str) -> bool:
    """Does this `<script>` really import `vue-router`?

    An import, not a mention: the specifier list of the comment-stripped
    script. This is what the router test used to do with the substring
    `"vue-router" in script`, and a file that explains in its header why it
    does *not* import vue-router satisfied it — every component whose comment
    mentioned the package was graded D for the prose. Type-only specifiers
    count here on purpose; see the module docstring.
    """
    return any(spec == "vue-router" for _, spec in specifiers(strip_comments(script)))


def router_tags(template: str) -> list[str]:
    """The template tags that need vue-router installed, as written.

    `<router-view>`/`<router-link>` are globals vue-router registers, and only
    an application that installed it resolves them: in a tree without a router
    they are a "failed to resolve component" warning and an empty shell. The
    script half of the grade has always seen them (the import, the composable);
    the template half saw nothing, so a page whose only tie to the router was
    the link it rendered was called standalone-ready.
    """
    return [name for name in tag_names(template)
            if name.lower().replace("-", "") in ROUTER_TAGS]


def grade_component(root: Path, path: Path, reach: set[Path] | None = None) -> Grade:
    """Grade the component at `path` (a `.vue` or a `.ts` file under src).

    `reach` is `api_reach(root)` — passed in when grading many files, because
    building it reads the whole tree and grading one file should not.

    `root` is resolved for the same reason as in `api_reach`: an API root is
    recognised by `relative_to(root)`, which a relative root silently fails.
    """
    root = root.resolve()
    src = repo_src(root)
    if reach is None:
        reach = api_reach(root)
    text = path.read_text(encoding="utf-8", errors="replace")
    script = "\n".join(SCRIPT_BLOCK.findall(text))
    template = "\n".join(template_blocks(text))
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
            # `reach` holds resolved paths; resolve here too, so a caller that
            # passes an unresolved root cannot silently disable the chain test.
            resolved.resolve() in reach
            and not rel.startswith("frontend/src/stores/")
            # `.vue` counts as an edge too: a page that renders a child which
            # fetches needs the network exactly as much as one that fetches
            # itself, and `api_reach` already closed over `.vue` edges — the
            # suffix test here was the only place the two disagreed.
            and resolved.suffix in (".ts", ".vue")
        ):
            api_direct = True  # reaches the network through a chain
            reasons.append(f"reaches the API layer through {rel}")

    stores = {normalise_store(name) for name in STORE_USE.findall(whole)}
    rendered_router_tags = router_tags(template)
    hard = bool(
        ROUTER_USE.search(whole)
        or imports_router(script)
        or rendered_router_tags
        or PARENT_USE.search(text)
        or BUS_USE.search(text)
        or INJECT_USE.search(script)
        or PROVIDE_USE.search(script)
    )
    if ROUTER_USE.search(whole) or imports_router(script):
        reasons.append("reads the route (useRoute/useRouter/$router/vue-router)")
    if rendered_router_tags:
        tag = rendered_router_tags[0]
        reasons.append(f"renders <{tag}>, which only resolves with a router installed")
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
