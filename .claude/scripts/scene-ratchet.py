#!/usr/bin/env python3
"""scene-ratchet.py — a scene that runs standalone may not stop running standalone.

    python3 .claude/scripts/scene-ratchet.py                check, against origin/main
    python3 .claude/scripts/scene-ratchet.py --base <ref>   check against the merge base with <ref>
    python3 .claude/scripts/scene-ratchet.py --list         every scene, its grade, its reasons
    python3 .claude/scripts/scene-ratchet.py --json         one JSON record on stdout, same exit code
    python3 .claude/scripts/scene-ratchet.py --self-test    prove it catches what it claims

WHY A GATE AND NOT A COUNT. `docs/manual/dev/scenes.md` says which scenes can be
rendered from props alone today. A document is a photograph: it was true when it
was written and nothing tells you when it stops being true, and the thing that
makes it false — a page that starts fetching, a panel that starts reading the
route — is exactly the change nobody notices. So the set is read off the tree
and checked instead of described.

WHAT IS FROZEN, AND WHERE IT COMES FROM. The tree at `git merge-base HEAD
--base` (default `origin/main`), graded by this same rule. A scene that is
standalone-ready IN THAT TREE is frozen `ready` and may not stop being; a scene
that is not is pre-existing `debt` and may sit there; a scene in neither —
because that tree does not have it — is NEW and must be standalone-ready from
its first commit. Nothing here is written down, so there is nothing to edit: a
branch cannot grandfather a scene by naming it, and there is no `--update`.

  The ledger this check used to keep (`frontend/scene-baseline.json`) was a
  cache of exactly that set, and it behaved like one: forty-odd commits in
  ninety days touched it and nearly every branch that split a page had to
  rewrite it, so two of them in flight at once conflicted on it every time. The
  repo already judges scene debt against the merge base
  (`scene-debt-ratchet.py --base origin/main`, and frontend.yml checks out with
  `fetch-depth: 0` for it), so this is that convention rather than a second one.
  Reading the merge base also makes the freeze a fact about the branch instead
  of a file the branch can edit.

  What it costs: the merge base has to be readable. A `--base` that does not
  resolve, or a tree without `frontend/src`, is exit 2 — never a pass.

THE RULE, in two halves:

  1. A scene that IS standalone-ready in that tree may not stop being. The
     frozen set may only grow.
  2. A scene that is NEW — not in the tree the branch started from — must be
     standalone-ready from its first commit. New pages and new panels are the
     only place a ratchet can be a rule instead of a description: the debt that
     is already here is grandfathered and may sit there, but nothing new may
     join it.

  A NEW PAGE MAY BE A CONTAINER. A route has to get its data from somewhere,
  and a page is where a route lands: reading the address, fetching, saving —
  that is a page's job, and forbidding it outright leaves a new page nowhere to
  put it. So a page may keep that job if it hands the rendering to a view: a
  sibling `<Page>View.vue` that the page imports AND renders. The import alone
  proves nothing — a page can import a view and render something else
  entirely — so the template must use the view's tag, and every other
  component the template renders must be standalone too: the view is where
  the rendering lives, not one of several places. Reading the template takes
  some care, and every shortcut here was a real impostor once: a tag in an
  HTML comment is not rendered; a `<template v-if>` nests and a scan that
  stops at the inner close loses what follows; `title="</template>"` is an
  attribute value, not a tag; a lowercase `<child />` is a component, native
  only if it is a real HTML/SVG element; Vuetify is trusted by the repo's own
  auto-import table (vuetify's `importMap.json`), never by the V- prefix; and
  a local binding shadows a builtin — an import (`import RouterView from
  './x.vue'`, graded like any other) or a declaration (`const RouterView =
  ...`, which nothing can prove, so it is not exempted). What the check
  cannot see through (a `<component :is>`, a tag no import explains) is not
  paired — a verifiable shape is the price of the exemption. The view is then
  a scene of its own, graded and frozen like any other — it is the part that
  must render from props alone — and the page is judged as its container, not
  as a scene that failed. It is the shape the recipe below already describes
  (`PanelDoc` -> `usePanelDoc` -> `PanelDocView`), named so a check can find it.
  Pages only: a route has to get its data somewhere, a panel does not — a
  panel in the same costume is a panel that fetches.

  Debt is therefore a set read off the base tree, not a count: it is what makes
  "new" decidable. A scene is pre-existing debt when the base tree had it and it
  was not standalone-ready, and new when the base tree did not have it. Neither
  set is maintained by hand — both are graded afresh on every run — which is
  also why nothing can move a scene between them by writing its name down.

WHAT A SCENE IS. Two kinds, both taken from the tree rather than from a list:

  * A PAGE is a `.vue` under `frontend/src/views/` that the router reaches.
    The router's import graph is walked from every `frontend/src/router/**/*.ts`
    (spec files excluded), through every `.ts` module it reaches, and any `.vue`
    under `views/` on the way is a page. A new page therefore needs no edit here
    to be judged — registering it in the router is what makes it a scene.
  * A PANEL is every `.vue` under `frontend/src/components/panels/`. There is no
    registry to keep in step; the directory is the set.
  * A VIEW is the `<Page>View.vue` beside a page that the page imports and
    renders: the rendering half of a container page (above). It is found from
    the page, so it too needs no registry.

STANDALONE-READY means grade A from `.claude/scripts/frontend_grade.py` — the
same function `arch-metrics.py` reports (so the board and the gate cannot
disagree about a scene). The grade is a regex estimate, not a compiler, and its
blind spots are documented there.

WHAT A FAILURE LOOKS LIKE, and what to do about it: `docs/manual/dev/scenes.md`
has the rule in Chinese and the recipe for pulling a fetch or a route read out
of a scene; the short version is that data comes in as props and intent goes out
as an event, with the fetch left in a composable the page calls.

WHAT THIS CHECK DOES NOT ASK: whether a standalone-ready scene is in the
preview site (`/demo/catalog`). That used to be a warning here, counted
against `catalog.ts` alone — it missed the entries split into the sibling
`catalog*.ts` files, and it only looked at scenes when every grade-A component
is a candidate. It is its own ratchet now, `.claude/scripts/catalog-ratchet.py`
(`pnpm run lint:catalog`), over every component rather than every scene.

Exit codes: 0 nothing to report, 1 a scene regressed or a new scene is not
standalone-ready, 2 could not judge (no `frontend/src/` under `--root`, no
merge base with `--base`, a base tree that cannot be read or graded, an
unreadable scene file). 2 is never a pass.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import re
import subprocess
import sys
import tarfile
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

#: Where the frozen set is read from: the merge base with this ref.
DEFAULT_BASE = "origin/main"

#: A spec file is not a scene and not a router module to walk from.
SPEC = re.compile(r"\.(spec|test)\.(ts|js|vue)$")

#: An import statement with its whole clause: `import A, { b, c as d } from 'x'`.
IMPORT_CLAUSE = re.compile(
    r"""import\s+(type\s+)?([^'"]*?)\s+from\s+['"]([^'"]+)['"]""", re.DOTALL
)

#: Tags a template may render without an import the checker can grade: Vue
#: and vue-router builtins. A local binding wins over these names — see
#: `paired_views`.
GLOBAL_TAGS = {
    "RouterView",
    "RouterLink",
    "Suspense",
    "Teleport",
    "Transition",
    "TransitionGroup",
    "KeepAlive",
    "Component",
}

#: Real HTML and SVG element names, lowercased (SVG's camelCase included:
#: `clipPath` is `clippath` here). Only these may be skipped as native — a
#: lowercase tag outside the list (`<child />`) is a component in Vue.
NATIVE_TAGS = {
    # HTML
    "a", "abbr", "address", "area", "article", "aside", "audio", "b", "base",
    "bdi", "bdo", "blockquote", "body", "br", "button", "canvas", "caption",
    "cite", "code", "col", "colgroup", "data", "datalist", "dd", "del",
    "details", "dfn", "dialog", "div", "dl", "dt", "em", "embed", "fieldset",
    "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5",
    "h6", "head", "header", "hgroup", "hr", "html", "i", "iframe", "img",
    "input", "ins", "kbd", "label", "legend", "li", "link", "main", "map",
    "mark", "menu", "meta", "meter", "nav", "noscript", "object", "ol",
    "optgroup", "option", "output", "p", "picture", "pre", "progress", "q",
    "rp", "rt", "ruby", "s", "samp", "script", "search", "section", "select",
    "slot", "small", "source", "span", "strong", "style", "sub", "summary",
    "sup", "table", "tbody", "td", "template", "textarea", "tfoot", "th",
    "thead", "time", "title", "tr", "track", "u", "ul", "var", "video", "wbr",
    # SVG (lowercased)
    "svg", "animate", "animatemotion", "animatetransform", "circle",
    "clippath", "defs", "desc", "discard", "ellipse", "feblend",
    "fecolormatrix", "fecomponenttransfer", "fecomposite", "feconvolvematrix",
    "fediffuselighting", "fedisplacementmap", "fedistantlight", "fedropshadow",
    "feflood", "fefunca", "fefuncb", "fefuncg", "fefuncr", "fegaussianblur",
    "feimage", "femerge", "femergenode", "femorphology", "feoffset",
    "fepointlight", "fespecularlighting", "fespotlight", "fetile",
    "feturbulence", "filter", "foreignobject", "g", "image", "line",
    "lineargradient", "marker", "mask", "metadata", "mpath", "path",
    "pattern", "polygon", "polyline", "radialgradient", "rect", "set", "stop",
    "switch", "symbol", "text", "textpath", "tspan", "use", "view",
    # Vue's own builtin element; `<component :is>` is rejected before this.
    "component",
}

#: A local declaration: `const RouterView = ChildFetch` shadows the builtin
#: and must not inherit its trust.
LOCAL_DECL = re.compile(r"\b(?:const|let|var|function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)")

#: Vuetify's components, auto-registered by the build: not ours to grade, so
#: trusted — but by NAME, not by prefix. A globally registered `VReport` is
#: indistinguishable from `VBtn` until the names are checked. The list is the
#: repo's own auto-import table (vuetify 3.13.5, `dist/json/importMap.json`);
#: a name it does not have (`VOverflowBtn` — exported once, never in the
#: table) is not Vuetify.
VUETIFY_TAGS = {
    "VAlert", "VAlertTitle", "VApp", "VAppBar", "VAppBarNavIcon", "VAppBarTitle",
    "VAutocomplete", "VAvatar", "VBadge", "VBanner", "VBannerActions",
    "VBannerText", "VBottomNavigation", "VBottomSheet", "VBreadcrumbs",
    "VBreadcrumbsDivider", "VBreadcrumbsItem", "VBtn", "VBtnGroup", "VBtnToggle",
    "VCalendar", "VCard", "VCardActions", "VCardItem", "VCardSubtitle", "VCardText",
    "VCardTitle", "VCarousel", "VCarouselItem", "VCheckbox", "VCheckboxBtn",
    "VChip", "VChipGroup", "VClassIcon", "VCode", "VCol", "VColorPicker",
    "VCombobox", "VComponentIcon", "VConfirmEdit", "VContainer", "VCounter",
    "VDataIterator", "VDataTable", "VDataTableFooter", "VDataTableHeaders",
    "VDataTableRow", "VDataTableRows", "VDataTableServer", "VDataTableVirtual",
    "VDatePicker", "VDatePickerControls", "VDatePickerHeader", "VDatePickerMonth",
    "VDatePickerMonths", "VDatePickerYears", "VDefaultsProvider", "VDialog",
    "VDialogBottomTransition", "VDialogTopTransition", "VDialogTransition",
    "VDivider", "VEmptyState", "VExpandBothTransition", "VExpandTransition", "VExpandXTransition",
    "VExpansionPanel", "VExpansionPanelText", "VExpansionPanelTitle",
    "VExpansionPanels", "VFab", "VFabTransition", "VFadeTransition", "VField",
    "VFieldLabel", "VFileInput", "VFooter", "VForm", "VHotkey", "VHover", "VIcon", "VImg",
    "VInfiniteScroll", "VInput", "VItem", "VItemGroup", "VKbd", "VLabel",
    "VLayout", "VLayoutItem", "VLazy", "VLigatureIcon", "VList", "VListGroup",
    "VListImg", "VListItem", "VListItemAction", "VListItemMedia",
    "VListItemSubtitle", "VListItemTitle", "VListSubheader", "VLocaleProvider",
    "VMain", "VMenu", "VMessages", "VNavigationDrawer", "VNoSsr",
    "VNumberInput", "VOtpInput", "VOverlay", "VPagination",
    "VParallax", "VProgressCircular", "VProgressLinear", "VRadio", "VRadioGroup",
    "VRangeSlider", "VRating", "VResponsive", "VRow", "VScaleTransition",
    "VScrollXReverseTransition", "VScrollXTransition", "VScrollYReverseTransition",
    "VScrollYTransition", "VSelect", "VSelectionControl", "VSelectionControlGroup",
    "VSheet", "VSkeletonLoader", "VSlideGroup", "VSlideGroupItem",
    "VSlideXReverseTransition", "VSlideXTransition", "VSlideYReverseTransition",
    "VSlideYTransition", "VSlider", "VSnackbar", "VSnackbarQueue", "VSpacer",
    "VSparkline", "VSpeedDial", "VStepper", "VStepperActions", "VStepperHeader",
    "VStepperItem", "VStepperWindow", "VStepperWindowItem", "VSvgIcon", "VSwitch",
    "VSystemBar", "VTab", "VTable", "VTabs", "VTabsWindow", "VTabsWindowItem",
    "VTextField", "VTextarea", "VThemeProvider", "VTimePicker", "VTimePickerClock",
    "VTimePickerControls", "VTimeline", "VTimelineItem", "VToolbar",
    "VToolbarItems", "VToolbarTitle", "VTooltip", "VTreeview", "VTreeviewGroup",
    "VTreeviewItem", "VValidation", "VVirtualScroll", "VWindow", "VWindowItem",
}


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


def route_pages(root: Path) -> list[str]:
    """Router-reached pages, without panels or a container's sibling views."""
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

    return sorted(pages)


def scene_paths(root: Path, reach: set[Path] | None = None) -> list[str]:
    """Every scene, as a repo-relative path: pages, sibling views, then panels."""
    pages = set(route_pages(root))
    src = root / "frontend" / "src"
    panels = {
        p.relative_to(root).as_posix()
        for p in (src / "components" / "panels").rglob("*.vue")
        if p.is_file()
    }
    views = set(paired_views(root, pages, reach).values()) - pages
    return sorted(pages) + sorted(views) + sorted(panels)


def pascal(tag: str) -> str:
    """`settle-view` -> `SettleView`; an already-Pascal tag is unchanged."""
    return "".join(part[:1].upper() + part[1:] for part in tag.split("-"))


def camelize(tag: str) -> str:
    """`settle-view` -> `settleView`; an already-camel tag is unchanged."""
    head, *rest = tag.split("-")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def resolve_names(tag: str) -> tuple[str, str, str]:
    """The names Vue resolves a tag to, in Vue's order: as written, camelized,
    PascalCased (`<router-view />` tries `router-view`, `routerView`,
    `RouterView`)."""
    return tag, camelize(tag), pascal(tag)


def import_locals(clause: str) -> list[str]:
    """The local names an import clause binds: `A, { b, c as d }` -> `[A, b, d]`."""
    clause = clause.strip()
    locals_: list[str] = []
    if clause.startswith("*"):
        match = re.match(r"\*\s+as\s+([A-Za-z0-9_]+)", clause)
        return [match.group(1)] if match else []
    if clause.startswith("{"):
        named = clause.strip("{} \n")
    else:
        head, _, brace = clause.partition("{")
        default = head.strip().rstrip(",").strip()
        if default:
            locals_.append(default)
        named = brace.strip("} \n")
    for part in named.split(","):
        part = re.sub(r"^type\s+", "", part.strip())
        if part:
            locals_.append(part.split(" as ")[-1].strip())
    return locals_


def template_tags(grade_module: Any, text: str) -> set[str]:
    """Component tags the template renders, as written.

    A tag is native only when it is a real HTML/SVG element: a lowercase
    `<child />` is a component in Vue, not an element. Only real tags count —
    `title="<SettleView />"` is an attribute value, not a render. Matching
    happens in `paired_views` under every name Vue resolves (see
    `resolve_names`).
    """
    tags: set[str] = set()
    for block in grade_module.template_blocks(text):
        for tag in grade_module.tag_names(block):
            if tag[:1].islower() and "-" not in tag and tag.lower() in NATIVE_TAGS:
                continue  # a real HTML/SVG element
            tags.add(tag)
    return tags


def paired_views(
    root: Path, pages: set[str], reach: set[Path] | None = None
) -> dict[str, str]:
    """`{page: view}` for every page that hands its rendering to a view.

    The view is the sibling `<Page>View.vue`, and three things must hold —
    an import alone proves nothing, since a page can import a view and render
    something else entirely:

      1. the page value-imports the view (`import type` is no import at all);
      2. the template actually renders the view's tag;
      3. every other component tag the template renders is a builtin
         (RouterView, Suspense, …), a Vuetify `v-*`, a package component, or
         resolves to a grade-A `.vue` under `src` — the view is where the
         rendering lives, not one of several places.

    What the check cannot see through — a `<component :is>`, a tag no import
    explains — is not paired: a verifiable shape is the price of the
    exemption, and the page is judged as an ordinary scene instead.
    """
    src = root / "frontend" / "src"
    grade_module = load_frontend_grade()
    if reach is None:
        reach = grade_module.api_reach(root)
    pairs: dict[str, str] = {}
    for rel in sorted(pages):
        page = root / rel
        view = page.with_name(f"{page.stem}View.vue")
        if not view.is_file():
            continue
        try:
            text = page.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise Unjudgeable(f"cannot read {rel}: {exc}") from exc

        # (1) the value import, and the local names every import binds.
        view_names: set[str] = set()
        imported: dict[str, Path | None] = {}
        for type_only, clause, spec in IMPORT_CLAUSE.findall(text):
            if type_only:
                continue
            resolved = grade_module.resolve_spec(spec, page, src)
            for local in import_locals(clause):
                imported[local] = resolved
                if resolved == view:
                    view_names.add(local)
        if not view_names:
            continue

        # (2) the template must render the view, and nothing unverifiable.
        names = [name for block in grade_module.template_blocks(text)
                 for name in grade_module.tag_names(block)]
        if any(name.lower() == "component" for name in names):
            continue  # <component :is> — what it renders cannot be seen
        tags = template_tags(grade_module, text)
        if not any(name in view_names for tag in tags for name in resolve_names(tag)):
            continue

        # (3) every other rendered component is standalone or not ours. Vue's
        # resolution order decides what a tag IS: the first name a local
        # import binds wins, and its grade is the verdict — there is no
        # falling back to a builtin's trust past a real binding. A local
        # declaration makes the name unprovable, and what the check cannot
        # prove is not exempted. Only a tag no binding claims may be a
        # builtin or Vuetify.
        script = "\n".join(grade_module.SCRIPT_BLOCK.findall(text))
        shadows = set(LOCAL_DECL.findall(script))

        def verifiable(
            tag: str,
            imported: dict[str, Path | None] = imported,
            shadows: set[str] = shadows,
        ) -> bool:
            names = resolve_names(tag)
            for name in names:
                if name in imported:
                    target = imported[name]
                    if target is None:
                        return True  # a package component: not ours to grade
                    if target.suffix != ".vue":
                        return False
                    try:
                        return grade_module.grade_component(root, target, reach).standalone
                    except OSError:
                        return False
                if name in shadows:
                    return False  # a local declaration hides what this name is
            return any(name in GLOBAL_TAGS or name in VUETIFY_TAGS for name in names)

        rest = [tag for tag in tags
                if not any(name in view_names for name in resolve_names(tag))]
        if all(verifiable(tag) for tag in rest):
            pairs[rel] = view.relative_to(root).as_posix()
    return pairs


def grade_scenes(root: Path, reach: set[Path] | None = None) -> dict[str, Any]:
    """Grade every scene. Returns `{scene: Grade}` — one `api_reach` for all."""
    grade_module = load_frontend_grade()
    if reach is None:
        reach = grade_module.api_reach(root)
    grades = {}
    for rel in scene_paths(root, reach):
        path = root / rel
        if not path.is_file():
            raise Unjudgeable(f"scene {rel} is not a file")
        try:
            grades[rel] = grade_module.grade_component(root, path, reach)
        except OSError as exc:
            raise Unjudgeable(f"cannot read {rel}: {exc}") from exc
    return grades


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
    #: pages that are not grade A but hand their rendering to a view that is: (key, view key)
    containers: list[tuple[str, str]] = field(default_factory=list)
    ready: list[str] = field(default_factory=list)
    debt: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.regressions and not self.new_debt


def container_view(scene: str, grades: dict[str, Any], views: dict[str, str]) -> str | None:
    """The standalone-ready view this scene renders through, if it is a container."""
    view = views.get(scene)
    if view is not None and view in grades and grades[view].standalone:
        return view
    return None


def judge(
    baseline: Baseline,
    grades: dict[str, Any],
    views: dict[str, str] | None = None,
) -> Verdict:
    """Compare the tree against the baseline. Pure, so it is testable."""
    verdict = Verdict()
    views = views or {}
    for scene in sorted(grades):
        key = key_of(scene)
        grade = grades[scene]
        view = None if grade.standalone else container_view(scene, grades, views)
        if view is not None:
            # A container: the page does the fetching, its view does the
            # rendering, and the view is the scene that is frozen. A page that
            # was frozen itself has moved its rendering out, which is the
            # recipe, not a regression; a page that was debt has paid it.
            verdict.containers.append((key, key_of(view)))
            if key in baseline.known:
                verdict.improvements.append(
                    (key, f"is a container now; its view {key_of(view)} is what is frozen")
                )
            continue
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

    return verdict


# ----------------------------------------------------------------- the freeze


def merge_base(root: Path, base: str) -> str:
    """The commit `HEAD` and `base` last agreed on: where the freeze is read.

    Not `base` itself — a branch may be behind it, and what a branch is judged
    against is the tree it actually started from. Nothing about a ref that does
    not resolve is a guess: it is exit 2.
    """
    result = subprocess.run(
        ["git", "-C", str(root), "merge-base", "HEAD", base],
        capture_output=True,
        text=True,
    )
    revs = result.stdout.split()
    if result.returncode or not revs:
        why = (result.stderr or result.stdout).strip().splitlines()
        raise Unjudgeable(
            f"no merge base between HEAD and {base}"
            + (f": {why[-1]}" if why else "")
            + " — the frozen set is read from that tree (a fetch may be missing)"
        )
    # Several best common ancestors (a criss-cross history) is not a tree to
    # guess at: any one of them is a real answer, so take the first.
    return revs[0]


def _extract_kwargs() -> dict[str, str]:
    """`filter="data"` where the interpreter has it.

    It refuses absolute paths and `..` out of the archive — which git does not
    write, and a corrupted object could. Older interpreters take no filter.
    """
    return {"filter": "data"} if hasattr(tarfile, "data_filter") else {}


def base_tree(root: Path, rev: str, dest: Path) -> Path:
    """`rev`'s `frontend/`, unpacked under `dest`. Returns `dest`.

    `git archive` into a scratch directory rather than `git worktree add`:
    this runs from more than one checkout of the same clone at once, and a
    worktree writes an entry into the shared `.git` that nothing prunes if the
    run dies before it can remove it.
    """
    archive = subprocess.run(
        ["git", "-C", str(root), "archive", "--format=tar", rev, "frontend"],
        capture_output=True,
    )
    if archive.returncode:
        why = archive.stderr.decode("utf-8", "replace").strip().splitlines()
        raise Unjudgeable(
            f"cannot read frontend/ at {rev}" + (f": {why[-1]}" if why else "")
        )
    try:
        with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
            tar.extractall(dest, **_extract_kwargs())
    except (tarfile.TarError, OSError) as exc:
        raise Unjudgeable(f"cannot unpack the tree at {rev}: {exc}") from exc
    if not (dest / "frontend" / "src").is_dir():
        raise Unjudgeable(f"the tree at {rev} has no {SRC_DIR}/ to grade")
    return dest


def classify(grades: dict[str, Any], views: dict[str, str]) -> Baseline:
    """The frozen set a graded tree implies: grade A is ready, the rest is debt.

    A container page is in neither: it is judged as its view (see `judge`), and
    its view is the scene that carries the freeze.
    """
    ready: set[str] = set()
    debt: set[str] = set()
    for scene, grade in grades.items():
        key = key_of(scene)
        if not grade.standalone and container_view(scene, grades, views) is not None:
            continue
        (ready if grade.standalone else debt).add(key)
    return Baseline(ready=frozenset(ready), debt=frozenset(debt))


def survey(root: Path) -> tuple[dict[str, Any], dict[str, str]]:
    """Grade every scene under `root`, and pair the pages that render a view."""
    grade_module = load_frontend_grade()
    reach = grade_module.api_reach(root)
    grades = grade_scenes(root, reach)
    # Pages only: the container rule exists because a ROUTE has to get its data
    # somewhere, and a panel has no route — a panel with a sibling
    # `<Panel>View.vue` is a panel that fetches, not a container.
    views = paired_views(root, {rel for rel in grades if rel.startswith(VIEWS_DIR)}, reach)
    return grades, views


def frozen(root: Path, base: str) -> tuple[Baseline, str]:
    """The frozen set, and a label naming the tree it was read from.

    The base tree is graded by this same rule — one ruler, two trees — so that
    "the scene dropped below the level it had there" compares two readings of
    one ruler rather than the readings of two.
    """
    rev = merge_base(root, base)
    with tempfile.TemporaryDirectory(prefix="scene-base-") as scratch:
        try:
            grades, views = survey(base_tree(root, rev, Path(scratch)))
        except LookupError as exc:
            # The grader refuses a tree without `frontend/src`; `base_tree`
            # has already checked, so this is the same answer one level down.
            raise Unjudgeable(f"cannot grade the tree at {rev}: {exc}") from exc
    return classify(grades, views), f"{base} ({rev[:12]})"


#: The repair, for the one branch that names a scene but not how to fix it (a
#: regression). Short on purpose: the page keeps
#: the read and the view takes the result, which is the one thing a reader has to
#: know before the fuller recipe.
FIX_SLICE = (
    "  Keep the API/route/store read where it belongs — the page (or a composable)\n"
    "  — and render through a sibling <PageName>View.vue that takes the values as\n"
    "  props and emits the intent up; a panel's read goes to the caller that renders\n"
    "  it. Recipe: docs/manual/dev/scenes.md#make-standalone; scaffold the View half\n"
    "  with `pnpm run scene:split -- src/views/<Page>.vue`."
)


def format_report(verdict: Verdict, baseline: Baseline, origin: str) -> str:
    """The human half: what is wrong, why, and the one thing that fixes it."""
    lines: list[str] = []
    if verdict.new_debt:
        lines.append("New scenes that are not standalone-ready (this is what the ratchet blocks):")
        for key, letter, reasons in verdict.new_debt:
            lines.append(f"  {key}: {letter}")
            lines.extend(f"    - {reason}" for reason in reasons)
        lines.append("")
        lines.append("A scene added from today on must render from props and emits alone:")
        lines.append("no API layer, no route, no business store. A page may keep the fetching")
        lines.append("if it renders through a sibling <Page>View.vue that does: the view is")
        lines.append("then the scene, and it must be grade A. How, in Chinese, with the recipe")
        lines.append("for a page and a panel: docs/manual/dev/scenes.md.")
        lines.append("")
    if verdict.regressions:
        lines.append("Scenes that stopped being standalone-ready (also what the ratchet blocks):")
        for key, letter, reasons in verdict.regressions:
            lines.append(f"  {key}: A -> {letter}")
            lines.extend(f"    - {reason}" for reason in reasons)
        lines.append("")
        lines.append("These were standalone-ready in the tree this branch started from")
        lines.append(f"({origin}), so putting the reach-through back is not an option. Fix:")
        lines.append(FIX_SLICE)
        lines.append("")
    if verdict.improvements:
        lines.append("Better than that tree says — nothing to do:")
        for key, why in verdict.improvements:
            lines.append(f"  {key}: {why}")
        lines.append("")
        lines.append("The freeze is read from the merge base, so this one is frozen on its own")
        lines.append("once it is in the tree both branches agree on.")
        lines.append("")
    lines.append(
        f"{len(verdict.ready)} standalone-ready, {len(verdict.containers)} container(s), "
        f"{len(verdict.debt)} pre-existing debt, "
        f"frozen from {origin}: {len(baseline.ready)} ready and {len(baseline.debt)} debt"
    )
    if not verdict.ok:
        lines.append("")
        lines.append(f"There is no list to edit: the freeze is read from {origin} on every")
        lines.append("run, so the fix has to be in the scene.")
    return "\n".join(lines)


def debt_details(
    grades: dict[str, Any], views: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """One row per scene that is not standalone-ready, for the JSON record.

    The scenes that are fine are not listed: the record is expanded to find out
    what to work on, and 128 rows of "grade A" would bury the ten that are not.

    A container page is left out too: it is judged as the container of a view
    that is standalone-ready, so it is not one of the scenes costing a slot —
    listing it would put a row in the record that the count above does not have.
    """
    views = views or {}
    return [
        {
            "file": key_of(scene),
            "grade": grades[scene].letter,
            "reasons": list(grades[scene].reasons),
        }
        for scene in sorted(grades)
        if not grades[scene].standalone and container_view(scene, grades, views) is None
    ]


def run(root: Path, base: str, *, listing: bool) -> int:
    """Judge the tree at `root` against the tree it started from, and report."""
    try:
        grades, views = survey(root)
        baseline, origin = frozen(root, base)
    except Unjudgeable as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        cannot_judge("scene-ratchet", exc)

    verdict = judge(baseline, grades, views)
    if as_json():
        # --json keeps stdout the one record line; on failure the same human
        # report (with the recipe) rides on stderr so a JSON caller still sees it.
        if not verdict.ok:
            print(format_report(verdict, baseline, origin), file=sys.stderr)
        # The unit here is a scene, and a scene costs at most one: `frozen` is
        # 1 for an allowance the base tree carries and `actual` is 0 once the
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
                details=debt_details(grades, views),
            )
        )
        return 0 if verdict.ok else 1

    if listing:
        for scene in sorted(grades):
            view = None if grades[scene].standalone else container_view(scene, grades, views)
            if view is not None:
                print(f"{grades[scene].letter} {key_of(scene)}  (container of {key_of(view)})")
                continue
            print(f"{grades[scene].letter} {key_of(scene)}")
            if not grades[scene].standalone:
                for reason in grades[scene].reasons:
                    print(f"    - {reason}")
        print()
    print(format_report(verdict, baseline, origin))
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
}

def _fixture(root: Path, changes: dict[str, str] | None = None) -> None:
    """Reset `root`'s fixture tree, then apply `changes` (a value of '' deletes).

    Anything the previous case left under `frontend/src` goes first. A leftover
    page is not a scene unless the router reaches it, but the tree a case
    commits as its base has to be the tree the case means.
    """
    src = root / "frontend" / "src"
    if src.is_dir():
        for path in sorted(src.rglob("*"), reverse=True):
            if path.is_file():
                path.unlink()
    files = dict(FIXTURE)
    files.update(changes or {})
    for rel, body in files.items():
        path = root / rel
        if body == "":
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git in the fixture repo with this machine's setup kept out of it.

    The self-test commits into a throwaway repo. A global `core.hooksPath`, a
    global identity or a global ignore file would each make the fixture tree
    depend on whose machine ran it, so none of the three apply here.
    """
    return subprocess.run(
        [
            "git", "-C", str(root),
            "-c", "user.name=scene-ratchet self-test",
            "-c", "user.email=self-test@example.invalid",
            "-c", "commit.gpgsign=false",
            "-c", "core.hooksPath=/dev/null",
            "-c", "core.excludesFile=/dev/null",
            *args,
        ],
        capture_output=True,
        text=True,
    )


def _freeze(root: Path, changes: dict[str, str] | None = None) -> str:
    """Commit FIXTURE (+`changes`) as the tree both branches agree on.

    This is the whole of what `frontend/scene-baseline.json` used to be: the
    freeze is read off a tree now, so a case sets its base by committing one.
    Returns the commit, which is what `--base HEAD` then resolves to.
    """
    _fixture(root, changes)
    if not (root / ".git").is_dir():
        _git(root, "init", "-q", "-b", "main")
    _git(root, "add", "frontend")
    result = _git(root, "commit", "-q", "--allow-empty", "-m", "fixture")
    if result.returncode:
        raise RuntimeError(f"the fixture repo cannot commit: {result.stderr.strip()}")
    return _git(root, "rev-parse", "HEAD").stdout.strip()


def self_test() -> int:
    """Drive the real CLI over fixture trees and require each exit code."""
    failures: list[str] = []

    def check(label: str, got: Any, want: Any) -> None:
        if got != want:
            failures.append(f"{label}: got {got!r}, want {want!r}")

    def run_cli(root: Path, *args: str, base: str = "HEAD") -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--root", str(root),
             "--base", base, *args],
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

        # -- the fixture, and the tree both branches agree on -----------------
        #    The freeze is a tree now, so every case is a pair: the tree the
        #    branch started from (a commit), and the tree it leaves behind (the
        #    working files). `--base HEAD` makes the fixture repo's own history
        #    that pair. The empty commit below is a tree with no `frontend/`.
        _git(root, "init", "-q", "-b", "main")
        _git(root, "commit", "-q", "--allow-empty", "-m", "empty")
        empty_sha = _git(root, "rev-parse", "HEAD").stdout.strip()
        base_sha = _freeze(root)
        result = run_cli(root)
        check("the fixture's own tree passes", result.returncode, 0)
        #    `--list` names every scene, the verdict only ever names the ones it
        #    has something to say about — so the tree's membership is asked of
        #    the listing. A page reached only through a `.spec.ts` import is not
        #    a scene; it is A in the fixture, so a report would stay silent about
        #    it even if the walk had wrongly taken the spec file as an edge.
        listed = run_cli(root, "--list").stdout
        check("grades the pages the router reaches", "A src/views/Home.vue" in listed, True)
        check("does not judge a spec file's imports", "src/views/NotAPage.vue" in listed, False)

        # -- 1. a baselined scene that regresses -----------------------------
        _fixture(root, {"frontend/src/views/Home.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        result = run_cli(root)
        check("a baselined scene that regresses fails", result.returncode, 1)
        check("and is named with its old grade", "src/views/Home.vue: A -> C" in result.stdout, True)
        check("and says why", "imports the API layer" in result.stdout, True)
        # The regression branch names a fix and its landing spot, not only the
        # reason — the reach-through has to come out of the scene.
        check("and gives the fix", "<PageName>View.vue" in result.stdout, True)
        check("and points at the recipe", "scenes.md#make-standalone" in result.stdout, True)

        # --json (what ci:fast calls) stays the one record line on stdout, and
        # carries the same fix on stderr.
        result = run_cli(root, "--json")
        check("--json regressing scene stays one line", len(result.stdout.strip().splitlines()), 1)
        check("--json keeps the fix on stderr", "<PageName>View.vue" in result.stderr, True)

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
        result = run_cli(root)
        check("a new page that is not standalone-ready fails", result.returncode, 1)
        check("and is reported as new", "src/views/Brand.vue: D" in result.stdout, True)
        check("and says why", "reads the route" in result.stdout, True)
        # The freeze is a tree, so a new scene cannot be grandfathered by naming
        # it anywhere: the only fix the report can offer is in the scene.
        check("and points at the scene, not a list", "no list to edit" in result.stdout, True)

        # -- 3. a new page that IS standalone-ready --------------------------
        _fixture(root)
        _fixture(root, {
            "frontend/src/views/Brand.vue": (
                "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
                "<template><div>{{ n }}</div></template>\n"
            ),
            "frontend/src/router/index.ts": _router(brand),
        })
        result = run_cli(root)
        check("a new page that is standalone-ready passes", result.returncode, 0)
        check("and is offered to the baseline", "src/views/Brand.vue: is new and standalone-ready" in result.stdout, True)

        # -- 4. pre-existing debt --------------------------------------------
        _fixture(root)
        result = run_cli(root)
        check("pre-existing debt passes", result.returncode, 0)
        check("and is not offered as an improvement", "Heavy.vue: was debt" in result.stdout, False)

        # -- 5. a type-only import is not reach ------------------------------
        #    The control: the same shape as a VALUE import is graded C, and the
        #    type-only one is graded A. Without the control this case would pass
        #    for a grader that never looked at Typed.vue at all.
        _fixture(root)
        result = run_cli(root, "--list")
        check("a type-only import is not reach", "A src/views/Typed.vue" in result.stdout, True)
        check("the same import as a value is reach", "C src/views/Heavy.vue" in result.stdout, True)
        _fixture(root, {"frontend/src/views/Typed.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        result = run_cli(root)
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
        result = run_cli(root, "--list")
        check("the page that renders a fetching child is graded C",
              "C src/views/Wrapped.vue" in result.stdout, True)
        result = run_cli(root)
        check("so a new scene below ready is refused", result.returncode, 1)
        check("and no list can freeze it", "no list to edit" in result.stdout, True)

        # -- 9. a new page that is a container ---------------------------------
        #    It fetches and reads the route, which is a page's job, and renders
        #    through a sibling view that does neither. The view is the scene the
        #    rule is about. Each control below breaks exactly one of the three
        #    things the pairing needs: the view is imported, is a value import,
        #    and is grade A.
        settle = "  { name: 'settle', path: '/settle', component: () => import('@/views/Settle.vue') },\n"
        container = (
            '<script setup lang="ts">\nimport { useRoute } from \'vue-router\'\n'
            "import { go } from '@/direct'\nimport SettleView from './SettleView.vue'\n"
            "const route = useRoute()\nconst thing = go()\n</script>\n"
            '<template><SettleView :thing="thing" :id="route.params.id" /></template>\n'
        )
        pure_view = (
            '<script setup lang="ts">\ndefineProps<{ thing: unknown; id: string }>()\n'
            "defineEmits<{ save: [] }>()\n</script>\n<template><div>{{ id }}</div></template>\n"
        )
        _fixture(root)
        _fixture(root, {
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/router/index.ts": _router(settle),
        })
        result = run_cli(root)
        check("a new container page with a standalone view passes", result.returncode, 0)
        check("and its view is offered to the baseline",
              "src/views/SettleView.vue: is new and standalone-ready" in result.stdout, True)
        listing = run_cli(root, "--list").stdout
        check("--list names the page as the view's container",
              "src/views/Settle.vue  (container of src/views/SettleView.vue)" in listing, True)

        # The freeze moves when the tree that carries the rendering becomes the
        # tree both branches agree on: commit the container and its view, and
        # the view is the frozen scene while the page is in neither list.
        _freeze(root, {
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/router/index.ts": _router(settle),
        })
        result = run_cli(root)
        check("a tree that already has the container passes", result.returncode, 0)
        check("and the page is in neither list", "src/views/Settle.vue: D" in result.stdout, False)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": (
                '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
                "<template><div /></template>\n"
            ),
        })
        result = run_cli(root)
        check("and its view is the frozen scene now", result.returncode, 1)
        check("and is named as the regression", "src/views/SettleView.vue: A -> C" in result.stdout, True)
        _freeze(root)

        # the controls: an unused sibling, a type-only import, a view that fetches
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n", ""
            ).replace('<SettleView :thing="thing" :id="route.params.id" />', "<div />"),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("a view the page does not import does not make it a container", result.returncode, 1)
        check("so the page is reported as new debt", "src/views/Settle.vue: D" in result.stdout, True)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from", "import type SettleView from"),
        })
        result = run_cli(root)
        check("a type-only import of the view does not pair it", result.returncode, 1)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": (
                '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
                "<template><div /></template>\n"
            ),
        })
        result = run_cli(root)
        check("a container whose view fetches fails", result.returncode, 1)
        check("and the view is named", "src/views/SettleView.vue: C" in result.stdout, True)
        check("with the page", "src/views/Settle.vue: D" in result.stdout, True)
        for rel in ("frontend/src/views/Settle.vue", "frontend/src/views/SettleView.vue"):
            (root / rel).unlink(missing_ok=True)
        _fixture(root)

        # -- 10. a container in name only -------------------------------------
        #    Three impostors that satisfy "a value import of the view" — the
        #    pairing #2209 asked for — without the rendering moving: an import
        #    the template never renders, a template that renders the view next
        #    to a fetching child, and a frozen page shedding its freeze into a
        #    shell. Plus one bystander: a panel in the same costume, which the
        #    rule never meant to cover (the docs say pages).
        _fixture(root)
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />', "<div />"),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("an imported view the page never renders does not pair", result.returncode, 1)
        check("and the shell page is named", "src/views/Settle.vue: D" in result.stdout, True)
        check("and no container is claimed", "container of" in run_cli(
            root, "--list").stdout, False)

        # the template renders the view AND a fetching child: the view is one
        # of several places the rendering lives, which is not the recipe
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import ChildFetch from '@/components/ChildFetch.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><ChildFetch />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a view rendered next to a fetching child does not pair", result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control for that rule: the same template with a grade-A child
        # instead is exactly the recipe, and must stay green
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import PlainNote from '@/components/PlainNote.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><PlainNote />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/PlainNote.vue": (
                '<script setup lang="ts">\ndefineProps<{ n: number }>()\n</script>\n'
                "<template><div>{{ n }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a container whose other rendered child is grade A passes", result.returncode, 0)
        check("and is listed as a container",
              "src/views/Settle.vue  (container of src/views/SettleView.vue)"
              in run_cli(root, "--list").stdout, True)

        # a view rendered only through <component :is> cannot be verified, and
        # a verifiable shape is the price of the exemption
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<component :is="SettleView" :thing="thing" :id="route.params.id" />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("a view rendered only through <component :is> does not pair", result.returncode, 1)

        # a page that was frozen as plain A shedding its freeze into a shell: it
        # imports the view and never renders it, so the page is the scene and it
        # has stopped being standalone-ready. There is no writer to launder it.
        plain_a = (
            "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
            "<template><div>{{ n }}</div></template>\n"
        )
        _freeze(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": plain_a,
            "frontend/src/views/SettleView.vue": "",
        })
        check("a frozen page as plain A still passes", run_cli(root).returncode, 0)
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />', "<div />"),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("a frozen page that shells out a view it never renders fails",
              result.returncode, 1)
        check("and is named as the regression", "src/views/Settle.vue: A -> D" in result.stdout, True)
        check("and nothing offers to lift the freeze",
              "no list to edit" in result.stdout, True)
        check("so the next run says the same", run_cli(root).returncode, 1)

        # the control: really moving the rendering DOES move the freeze — once
        # the tree that carries it is the tree both branches agree on, the view
        # is the scene that is frozen
        _freeze(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": pure_view,
        })
        check("a frozen page that really moves its rendering passes",
              run_cli(root).returncode, 0)
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": (
                '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
                "<template><div /></template>\n"
            ),
        })
        result = run_cli(root)
        check("and the view is frozen now", result.returncode, 1)
        check("so its regression is the one named",
              "src/views/SettleView.vue: A -> C" in result.stdout, True)

        # a panel in the same costume: the container rule is for pages, whose
        # route has to get its data somewhere — a panel has no such excuse
        _fixture(root)
        _fixture(root, {
            "frontend/src/components/panels/Sneaky.vue": (
                '<script setup lang="ts">\nimport { go } from \'@/direct\'\n'
                "import SneakyView from './SneakyView.vue'\nconst thing = go()\n</script>\n"
                '<template><SneakyView :thing="thing" /></template>\n'
            ),
            "frontend/src/components/panels/SneakyView.vue": pure_view,
        })
        result = run_cli(root)
        check("a panel cannot be a container even when it really renders the view",
              result.returncode, 1)
        check("and is named as new debt", "src/components/panels/Sneaky.vue" in result.stdout, True)

        for rel in (
            "frontend/src/views/Settle.vue",
            "frontend/src/views/SettleView.vue",
            "frontend/src/components/panels/Sneaky.vue",
            "frontend/src/components/panels/SneakyView.vue",
            "frontend/src/components/PlainNote.vue",
            "frontend/src/components/ChildFetch.vue",
        ):
            (root / rel).unlink(missing_ok=True)
        _freeze(root)

        # -- 11. impostors at the extraction layer ----------------------------
        #    The pairing checks are only as good as what they read: a comment
        #    is not a render, a template scan that stops at the first nested
        #    </template> loses every tag after it, a V-prefix is not a Vuetify
        #    registration, and a local binding shadows a builtin name.
        _fixture(root)
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<div /><!-- TODO: <SettleView :thing="thing" :id="route.params.id" /> -->'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("a view rendered only in a comment does not pair", result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # a naive scan ends the template at the first nested </template> and
        # loses every tag after it
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import ChildFetch from '@/components/ChildFetch.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" />'
                '<template v-if="true"><div /></template><ChildFetch />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a fetching child after a nested template does not pair", result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control: the same shape with a grade-A child stays green
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import PlainNote from '@/components/PlainNote.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" />'
                '<template v-if="true"><div /></template><PlainNote />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/PlainNote.vue": (
                '<script setup lang="ts">\ndefineProps<{ n: number }>()\n</script>\n'
                "<template><div>{{ n }}</div></template>\n"
            ),
        })
        check("an A-grade child after a nested template still pairs",
              run_cli(root).returncode, 0)

        # a V-prefix is not a Vuetify registration: a globally registered
        # component the check cannot see must not be trusted
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><VReport />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("an unknown V-prefixed tag does not pair", result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control: a real Vuetify tag is trusted
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><v-btn>save</v-btn>'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        check("a real Vuetify tag still pairs", run_cli(root).returncode, 0)

        # a local binding shadows the builtin: `import RouterView from ...` is
        # that file, not the router's outlet, and must be graded
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import RouterView from '@/components/LocalFetch.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><RouterView />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/LocalFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a local binding named RouterView is graded, not trusted", result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control: the unimported builtin RouterView stays trusted
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><RouterView />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        check("the builtin RouterView still pairs", run_cli(root).returncode, 0)

        for rel in (
            "frontend/src/views/Settle.vue",
            "frontend/src/views/SettleView.vue",
            "frontend/src/components/PlainNote.vue",
            "frontend/src/components/ChildFetch.vue",
            "frontend/src/components/LocalFetch.vue",
        ):
            (root / rel).unlink(missing_ok=True)
        _freeze(root)

        # -- 12. impostors Vue itself would execute ---------------------------
        #    Each shape below is valid, compiled-and-renders Vue (checked
        #    against the repo's own compiler-sfc by review): a lowercase local
        #    component, a quoted `</template>` inside an attribute value, a
        #    local declaration shadowing a builtin, and a V-name the vuetify
        #    import map does not have. If the check cannot prove what a tag
        #    is, the tag is not exempted.
        _fixture(root)
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import child from '@/components/ChildFetch.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><child />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a lowercase local component is graded, not skipped as native",
              result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control: a lowercase import of a grade-A component still pairs
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import note from '@/components/PlainNote.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><note />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/PlainNote.vue": (
                '<script setup lang="ts">\ndefineProps<{ n: number }>()\n</script>\n'
                "<template><div>{{ n }}</div></template>\n"
            ),
        })
        check("a lowercase import of an A component still pairs",
              run_cli(root).returncode, 0)

        # a quoted `</template>` inside an attribute value is not a tag
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import ChildFetch from '@/components/ChildFetch.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" />'
                '<div title="</template>">x</div><ChildFetch />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a quoted </template> in an attribute does not end the scan",
              result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the same hole on the grader's side: a frozen page whose route read
        # sits after a nested template was graded A because the read was lost
        _fixture(root, {"frontend/src/views/Home.vue": (
            "<script setup lang=\"ts\">\ndefineProps<{ ok: boolean }>()\n</script>\n"
            '<template><template v-if="ok"><div /></template>'
            "<div>{{ $router.push('/') }}</div></template>\n"
        )})
        result = run_cli(root)
        check("a route read after a nested template still counts", result.returncode, 1)
        check("and the frozen page is named", "src/views/Home.vue: A -> D" in result.stdout, True)
        _fixture(root)

        # a local declaration shadows the builtin: `const RouterView = ...` is
        # not the router's outlet, and the check cannot prove what it renders
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import ChildFetch from '@/components/ChildFetch.vue'\n"
                "const RouterView = ChildFetch\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><RouterView />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a builtin shadowed by a local declaration is not trusted",
              result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # a name the vuetify import map does not have is not Vuetify, however
        # much it looks like it
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><VOverflowBtn />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("a V-name outside the vuetify import map does not pair",
              result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control: a less common name the import map really has is trusted
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><v-otp-input />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        check("a name the import map really has still pairs",
              run_cli(root).returncode, 0)

        for rel in (
            "frontend/src/views/Settle.vue",
            "frontend/src/views/SettleView.vue",
            "frontend/src/components/PlainNote.vue",
            "frontend/src/components/ChildFetch.vue",
        ):
            (root / rel).unlink(missing_ok=True)
        _freeze(root)

        # -- 13. the binding decides, and attributes do not render ------------
        #    Two more, reproduced green by review and compiled by the repo's
        #    own compiler-sfc: a local import under a trusted name (the check
        #    saw the miss, then fell through to the builtin's trust), and a
        #    `<SettleView />` that lives inside an attribute value (the tag
        #    scan read strings as renders).
        _fixture(root)
        for label, tag in (("camelCase", "<routerView />"), ("kebab", "<router-view />")):
            _fixture(root, {
                "frontend/src/router/index.ts": _router(settle),
                "frontend/src/views/Settle.vue": container.replace(
                    "import SettleView from './SettleView.vue'\n",
                    "import SettleView from './SettleView.vue'\n"
                    "import routerView from '@/components/ChildFetch.vue'\n").replace(
                    '<SettleView :thing="thing" :id="route.params.id" />',
                    f'<SettleView :thing="thing" :id="route.params.id" />{tag}'),
                "frontend/src/views/SettleView.vue": pure_view,
                "frontend/src/components/ChildFetch.vue": (
                    '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                    "<template><div>{{ r }}</div></template>\n"
                ),
            })
            result = run_cli(root)
            check(f"a local binding under a builtin name ({label}) decides",
                  result.returncode, 1)
            check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n",
                "import SettleView from './SettleView.vue'\n"
                "import vBtn from '@/components/ChildFetch.vue'\n").replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><v-btn />'),
            "frontend/src/views/SettleView.vue": pure_view,
            "frontend/src/components/ChildFetch.vue": (
                '<script setup lang="ts">\nconst r = await fetch(\'/api/things\')\n</script>\n'
                "<template><div>{{ r }}</div></template>\n"
            ),
        })
        result = run_cli(root)
        check("a local binding under a vuetify name decides", result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # an attribute value is not a render
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<div title="<SettleView />">x</div>'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root)
        check("a view named only inside an attribute value does not pair",
              result.returncode, 1)
        check("and the page is named", "src/views/Settle.vue: D" in result.stdout, True)

        # the control: attribute strings add no phantom tags either — a real
        # container whose other attribute mentions a component stays green
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" />'
                '<div title="<PlainNote />">x</div>'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        check("a tag string inside an attribute does not break a real container",
              run_cli(root).returncode, 0)

        # -- 14. what the template renders, and a comment that only mentions ---
        #    The router test is a real import plus the template's own tags. A
        #    page whose only tie to the router is the link it draws is not
        #    standalone — that is what a router tag needs an app for — and a
        #    comment naming the package is prose, not a dependency:
        #    `common/NavLink.vue` is the file the manual points at for *not*
        #    importing vue-router, and the substring test graded it D for the
        #    paragraph in its header that says so.
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/SettleView.vue": "",
            "frontend/src/views/Settle.vue": (
                '<script setup lang="ts">\ndefineProps<{ id: string }>()\n</script>\n'
                '<template><router-link to="/x">{{ id }}</router-link></template>\n'
            ),
        })
        result = run_cli(root)
        check("a page whose only router use is the tag it renders is not ready",
              result.returncode, 1)
        check("and it is named", "src/views/Settle.vue: D" in result.stdout, True)
        check("and the tag is the reason", "renders <router-link>" in result.stdout, True)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": (
                '<script setup lang="ts">\n'
                "// The host passes a NavLink's `to` down; no vue-router here.\n"
                "defineProps<{ id: string }>()\n</script>\n"
                "<template><div>{{ id }}</div></template>\n"
            ),
        })
        check("a comment that names vue-router is not a dependency",
              run_cli(root).returncode, 0)

        # -- 15. an edge spelled `export … from` ------------------------------
        #    An index barrel carries the chain: `index.ts` re-exports the
        #    composable that fetches and the page imports the barrel. Reading
        #    only `import` left that edge out, so such a page was accepted as
        #    "new and standalone-ready" — the one verdict that lets a fetching
        #    file into the frozen set. `views/tasks/composables/index.ts` is
        #    the real one, and `views/tasks/detail/Brief.vue` the real page.
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/SettleView.vue": "",
            "frontend/src/views/pages/composables/index.ts":
                "export { useThing } from './useThing'\n",
            "frontend/src/views/pages/composables/useThing.ts":
                "import { api } from '@/api'\nexport const useThing = () => api.get()\n",
            "frontend/src/views/Settle.vue": (
                '<script setup lang="ts">\n'
                "import { useThing } from '@/views/pages/composables'\n"
                "useThing()\n</script>\n<template><div /></template>\n"
            ),
        })
        result = run_cli(root)
        check("a page that fetches through a re-export barrel is not ready",
              result.returncode, 1)
        check("and it is named", "src/views/Settle.vue: C" in result.stdout, True)

        # the control: the same barrel re-exporting only a type is erased, so
        # the page that names the type is still standalone-ready
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/pages/composables/index.ts":
                "export type { Thing } from './useThing'\n",
            "frontend/src/views/Settle.vue": (
                '<script setup lang="ts">\n'
                "import type { Thing } from '@/views/pages/composables'\n"
                "defineProps<{ thing: Thing | null }>()\n</script>\n"
                "<template><div /></template>\n"
            ),
        })
        check("a barrel that re-exports only a type is not an edge",
              run_cli(root).returncode, 0)

        for rel in (
            "frontend/src/views/Settle.vue",
            "frontend/src/views/SettleView.vue",
            "frontend/src/components/ChildFetch.vue",
            "frontend/src/views/pages/composables/index.ts",
            "frontend/src/views/pages/composables/useThing.ts",
        ):
            (root / rel).unlink(missing_ok=True)
        _freeze(root)

        # -- 6. cannot judge --------------------------------------------------
        #    The one answer this gate exists to avoid is "no frozen set, so
        #    nothing is frozen, so everything is fine". A base that cannot be
        #    read is a 2, and one that cannot be resolved is a 2 before any of
        #    that.
        _fixture(root)
        result = run_cli(root)
        check("a healthy tree is judged", result.returncode, 0)
        result = run_cli(root, base="refs/heads/absent")
        check("a base ref that does not resolve cannot be judged", result.returncode, 2)
        check("and says why", "merge base" in result.stderr, True)
        check("and names the ref", "refs/heads/absent" in result.stderr, True)
        result = run_cli(root, "--json", base="refs/heads/absent")
        check("--json reports it too", result.returncode, 2)
        try:
            record = json.loads(result.stdout)
        except ValueError:
            record = {}
        check("as the record a collector reads", record.get("status"), "cannot_judge")
        result = run_cli(root, base=empty_sha)
        check("a base tree with no frontend/ cannot be judged", result.returncode, 2)
        check("and says what it could not read", "cannot read frontend/" in result.stderr, True)
        # a base tree that has `frontend/` and no `frontend/` src to grade: the
        # same family, one level in. Committed here, undone right after.
        _git(root, "rm", "-r", "-q", "--cached", "frontend/src")
        (root / "frontend" / "package.json").write_text('{"name": "fixture"}\n', encoding="utf-8")
        _git(root, "add", "frontend/package.json")
        _git(root, "commit", "-q", "-m", "no src")
        result = run_cli(root, base="HEAD")
        check("a base tree with no frontend/src cannot be judged", result.returncode, 2)
        check("and says so", "frontend/src" in result.stderr, True)
        _git(root, "reset", "-q", "--hard", base_sha)

        # -- 7. what the gate refuses, and what it may not do -----------------
        #    There is no writer any more, so no list can launder a regression or
        #    grandfather a new scene: the freeze is the base tree, and the only
        #    edit that changes the verdict is the scene.
        _fixture(root, {"frontend/src/views/Home.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        result = run_cli(root)
        check("a regression is refused", result.returncode, 1)
        check("and there is no list to edit", "no list to edit" in result.stdout, True)
        check("so re-running says the same thing", run_cli(root).returncode, 1)

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
        result = run_cli(root)
        check("a new scene below ready is refused", result.returncode, 1)
        check("and is named", "src/views/Brand.vue" in result.stdout, True)
        check("while the scene it replaced is only an improvement",
              "src/views/Home.vue: is no longer in the tree" in result.stdout, True)

        # Debt that got paid down, and a ready scene that is gone from the
        # candidate, are both fine: the first is better than the base tree, the
        # second is a scene the candidate simply does not have.
        _fixture(root, {
            "frontend/src/views/Heavy.vue": (
                "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
                "<template><div>{{ n }}</div></template>\n"
            ),
            "frontend/src/views/Nested.vue": "",
            "frontend/src/router/pages.ts": "export const pages = []\n",
        })
        result = run_cli(root)
        check("paid-down debt passes", result.returncode, 0)
        check("and is reported as better than the base tree",
              "src/views/Heavy.vue: was debt, is standalone-ready now" in result.stdout, True)
        check("and the scene that is gone is an improvement, not a failure",
              "src/views/Nested.vue: is no longer in the tree" in result.stdout, True)

        # -- 8. the wiring, which is what makes all of the above a gate -------
        #    A check nothing runs is a document with extra steps, and a run
        #    without `--base` freezes against whatever `origin/main` happens to
        #    be for the caller — or, in a shallow clone, against nothing.
        package = json.loads((REPO_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
        script = package["scripts"].get("lint:scenes", "")
        check("frontend/package.json runs this script against origin/main",
              script.endswith("scene-ratchet.py --base origin/main"), True)
        check("and keeps no writer", "lint:scenes:update" in package["scripts"], False)
        workflow = (REPO_ROOT / ".github" / "workflows" / "frontend.yml").read_text(encoding="utf-8")
        check("and CI runs that script", "run: pnpm run lint:scenes" in workflow, True)
        hooks = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        check("and a commit runs it with the same base",
              "scene-ratchet.py --base origin/main" in hooks, True)
        #    Whether the hook fires at all is `files:`. The pattern used to end
        #    at the directory -- `src/(views|router|components/panels)/$` -- so
        #    it matched no file that exists and the local gate never ran for a
        #    scene change. Pre-commit searches the whole path, so the answer is
        #    checked here rather than assumed from the string.
        block = re.search(r"- id: scene-ratchet\n(?:.*\n)*?\s+files: (\S+)", hooks)
        files = re.compile(block.group(1)) if block else None
        check("and that hook fires on what a scene change touches",
              files is not None
              and all(files.search(path) is not None for path in (
                  "frontend/src/views/Home.vue",
                  "frontend/src/router/index.ts",
                  "frontend/src/components/panels/catalog/CatalogTable.vue",
                  "frontend/package.json",
                  ".claude/scripts/scene-ratchet.py",
              ))
              and files.search("backend/app/main.py") is None, True)
        check("and the ledger is gone",
              (REPO_ROOT / "frontend" / "scene-baseline.json").exists(), False)
        fast = (REPO_ROOT / ".github" / "scripts" / "ci-fast.py").read_text(encoding="utf-8")
        merge_base_hooks = re.search(r"MERGE_BASE_HOOKS = \{([^}]*)\}", fast)
        check("and the quick tier knows not to run it without a merge base",
              merge_base_hooks is not None and "scene-ratchet" in merge_base_hooks.group(1), True)

        # -- 9. a wrong root is a 2, never a pass ---------------------------
        #    The trap this gate exists for: `frontend/` instead of the repo
        #    root has no `frontend/src` underneath, and an empty import graph
        #    would grade every component standalone — the silent-A failure.
        wrong = run_cli(root / "frontend")
        check("a root without frontend/src exits 2", wrong.returncode, 2)
        check("and says why", "frontend/src" in wrong.stderr, True)

    if failures:
        print("SELF-TEST FAIL:")
        for line in failures:
            print(f"  {line}")
        return 1
    print(
        "PASS: scene-ratchet self-test (a regressed scene, a new scene that is not "
        "ready, a container page and three ways of not being one, four container "
        "impostors — an unrendered import, a fetching co-child, a shelled freeze, "
        "a costumed panel — four more at the extraction layer — a view in a "
        "comment, a child lost to a nested template, a V-prefixed stranger, a "
        "builtin shadowed by a local binding — and four Vue itself executes — a "
        "lowercase component, a quoted </template>, a shadowing declaration, a "
        "V-name the import map lacks — and two more — a trusted name claimed by "
        "a local binding, a view mentioned only inside an attribute value — "
        "and two the router decides — a link the template draws against a "
        "comment that only names the package — and two the barrel decides — a "
        "re-export that carries the chain against one that carries only a type — "
        "with the controls that stay green, "
        "debt read off the base tree, debt paid down, a type-only import that "
        "is not reach, no list to launder a regression or a new scene, four ways "
        "of not being able to read the freeze, a hook pattern that really fires "
        "on a scene change, and a wrong root being a 2)"
    )
    return 0


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to judge")
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE,
        help=f"freeze the scenes from the merge base with this ref (default: {DEFAULT_BASE})",
    )
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
    try:
        return run(root, args.base, listing=args.list)
    except LookupError as exc:
        # The grader refuses to judge a root without `frontend/src` (an empty
        # import graph would grade everything standalone). Not a violation —
        # a 2, so a wrong root never looks like a pass.
        print(f"cannot judge: {exc}", file=sys.stderr)
        cannot_judge("scene-ratchet", exc)


if __name__ == "__main__":
    sys.exit(main())
