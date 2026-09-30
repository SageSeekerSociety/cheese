#!/usr/bin/env python3
"""scene-ratchet.py — a scene that runs standalone may not stop running standalone.

    python3 .claude/scripts/scene-ratchet.py               check (exit 1 on new debt)
    python3 .claude/scripts/scene-ratchet.py --update      the baseline may only grow
    python3 .claude/scripts/scene-ratchet.py --list        every scene, its grade, its reasons
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

  A NEW PAGE MAY BE A CONTAINER. A route has to get its data from somewhere,
  and a page is where a route lands: reading the address, fetching, saving —
  that is a page's job, and forbidding it outright leaves a new page nowhere to
  put it. So a page may keep that job if it hands the rendering to a view: a
  sibling `<Page>View.vue` that the page imports AND renders. The import alone
  proves nothing — a page can import a view and render something else
  entirely — so the template must use the view's tag, and every other
  component the template renders must be standalone too: the view is where
  the rendering lives, not one of several places. Reading the template takes
  some care: a tag in an HTML comment is not rendered, a `<template v-if>`
  nests and a scan that stops at the inner close loses what follows, Vuetify
  is trusted by component name and never by the V- prefix, and a local import
  shadows a builtin (`import RouterView from './x.vue'` is that file, graded
  like any other). What the check cannot see
  through (a `<component :is>`, a tag no import explains) is not paired — a
  verifiable shape is the price of the exemption. The view is then a scene of
  its own, graded and frozen like any other — it is the part that must render
  from props alone — and the page is judged as its container, not as a scene
  that failed. It is the shape the recipe below already describes
  (`PanelDoc` -> `usePanelDoc` -> `PanelDocView`), named so a check can find it.
  Pages only: a route has to get its data somewhere, a panel does not — a
  panel in the same costume is a panel that fetches.

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

#: An import statement with its whole clause: `import A, { b, c as d } from 'x'`.
IMPORT_CLAUSE = re.compile(
    r"""import\s+(type\s+)?([^'"]*?)\s+from\s+['"]([^'"]+)['"]""", re.DOTALL
)
#: A tag in a template: `<SettleView ...>` or `</settle-view>`.
TAG_USE = re.compile(r"</?([A-Za-z][A-Za-z0-9_-]*)")
#: What a template renders through `<component :is>` the check cannot see.
DYNAMIC_TAG = re.compile(r"<component\b", re.IGNORECASE)

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

#: Vuetify's components, auto-registered by the build: not ours to grade, so
#: trusted — but by NAME, not by prefix. A globally registered `VReport` is
#: indistinguishable from `VBtn` until the names are checked. The list follows
#: the repo's vuetify (3.9.3, `components/*/index.d.ts`, component names only).
VUETIFY_TAGS = {
    "VAlert", "VAlertTitle", "VApp", "VAppBar", "VAppBarNavIcon", "VAppBarTitle",
    "VAutocomplete", "VAvatar", "VBadge", "VBanner", "VBannerActions",
    "VBannerText", "VBottomNavigation", "VBottomSheet", "VBreadcrumbs",
    "VBreadcrumbsDivider", "VBreadcrumbsItem", "VBtn", "VBtnGroup", "VBtnToggle",
    "VCard", "VCardActions", "VCardItem", "VCardSubtitle", "VCardText",
    "VCardTitle", "VCarousel", "VCarouselItem", "VCheckbox", "VCheckboxBtn",
    "VChip", "VChipGroup", "VClassIcon", "VCode", "VCol", "VColorPicker",
    "VCombobox", "VComponentIcon", "VConfirmEdit", "VContainer", "VCounter",
    "VDataIterator", "VDataTable", "VDataTableFooter", "VDataTableHeaders",
    "VDataTableRow", "VDataTableRows", "VDataTableServer", "VDataTableVirtual",
    "VDatePicker", "VDatePickerControls", "VDatePickerHeader", "VDatePickerMonth",
    "VDatePickerMonths", "VDatePickerYears", "VDefaultsProvider", "VDialog",
    "VDialogBottomTransition", "VDialogTopTransition", "VDialogTransition",
    "VDivider", "VEmptyState", "VExpandTransition", "VExpandXTransition",
    "VExpansionPanel", "VExpansionPanelText", "VExpansionPanelTitle",
    "VExpansionPanels", "VFab", "VFabTransition", "VFadeTransition", "VField",
    "VFieldLabel", "VFileInput", "VFooter", "VForm", "VHover", "VIcon", "VImg",
    "VInfiniteScroll", "VInput", "VItem", "VItemGroup", "VKbd", "VLabel",
    "VLayout", "VLayoutItem", "VLazy", "VLigatureIcon", "VList", "VListGroup",
    "VListImg", "VListItem", "VListItemAction", "VListItemMedia",
    "VListItemSubtitle", "VListItemTitle", "VListSubheader", "VLocaleProvider",
    "VMain", "VMenu", "VMessages", "VNavigationDrawer", "VNoSsr",
    "VNumberInput", "VOtpInput", "VOverflowBtn", "VOverlay", "VPagination",
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


def scene_paths(root: Path, reach: set[Path] | None = None) -> list[str]:
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
    views = set(paired_views(root, pages, reach).values()) - pages
    return sorted(pages) + sorted(views) + sorted(panels)


def pascal(tag: str) -> str:
    """`settle-view` -> `SettleView`; an already-Pascal tag is unchanged."""
    return "".join(part[:1].upper() + part[1:] for part in tag.split("-"))


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
    """Component tags the template renders, PascalCased (`v-btn` -> `VBtn`)."""
    tags: set[str] = set()
    for block in grade_module.template_blocks(text):
        for tag in TAG_USE.findall(block):
            if tag[:1].islower() and "-" not in tag:
                continue  # a native element
            tags.add(pascal(tag))
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
        template = "\n".join(grade_module.template_blocks(text))
        if DYNAMIC_TAG.search(template):
            continue
        tags = template_tags(grade_module, text)
        if not tags & view_names:
            continue

        # (3) every other rendered component is standalone or not ours. A
        # local binding wins over a trusted name: `import RouterView from
        # './LocalFetch.vue'` shadows the router's outlet and must be graded.
        def verifiable(tag: str, imported: dict[str, Path | None] = imported) -> bool:
            if tag in imported:
                target = imported[tag]
                if target is None:
                    return True  # a package component: not ours to grade
                if target.suffix != ".vue":
                    return False
                try:
                    return grade_module.grade_component(root, target, reach).standalone
                except OSError:
                    return False
            if tag in GLOBAL_TAGS:
                return True  # a Vue / vue-router builtin
            return tag in VUETIFY_TAGS  # Vuetify by name, never by prefix

        rest = tags - view_names
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
    catalog: set[str],
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

    verdict.uncatalogued = sorted(key for key in verdict.ready if key not in catalog)
    return verdict


def tightened(
    baseline: Baseline,
    grades: dict[str, Any],
    *,
    bootstrap: bool = False,
    views: dict[str, str] | None = None,
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
        elif container_view(scene, grades, views or {}) is not None:
            # In neither list: its view carries the freeze, and the page is
            # judged as that view's container on every run.
            ready.discard(key)
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
        f"{len(verdict.ready)} standalone-ready, {len(verdict.containers)} container(s), "
        f"{len(verdict.debt)} pre-existing debt, "
        f"baseline has {len(baseline.ready)} ready and {len(baseline.debt)} debt"
    )
    if not verdict.ok:
        lines.append("")
        lines.append("A ratchet that can be satisfied by editing its baseline is not a ratchet:")
        lines.append("--update only ever adds to `ready` and drops from `debt`.")
    return "\n".join(lines)


def run(root: Path, baseline_path: Path, *, update: bool, listing: bool) -> int:
    """Judge the tree at `root` against `baseline_path` and print the answer."""
    try:
        grade_module = load_frontend_grade()
        reach = grade_module.api_reach(root)
        grades = grade_scenes(root, reach)
        # Pages only: the container rule exists because a ROUTE has to get
        # its data somewhere, and a panel has no route — a panel with a
        # sibling `<Panel>View.vue` is a panel that fetches, not a container.
        views = paired_views(
            root, {rel for rel in grades if rel.startswith(VIEWS_DIR)}, reach
        )
        bootstrap = update and not baseline_path.is_file()
        baseline = read_baseline(baseline_path, create_if_missing=update)
    except Unjudgeable as exc:
        print(f"cannot judge: {exc}", file=sys.stderr)
        return 2

    catalog = catalog_entries(root)

    if update:
        next_baseline, refusals = tightened(baseline, grades, bootstrap=bootstrap, views=views)
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

    verdict = judge(baseline, grades, catalog, views)
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
        result = run_cli(root, baseline_path)
        check("a new container page with a standalone view passes", result.returncode, 0)
        check("and its view is offered to the baseline",
              "src/views/SettleView.vue: is new and standalone-ready" in result.stdout, True)
        listing = run_cli(root, baseline_path, "--list").stdout
        check("--list names the page as the view's container",
              "src/views/Settle.vue  (container of src/views/SettleView.vue)" in listing, True)

        result = run_cli(root, baseline_path, "--update")
        check("--update accepts a container", result.returncode, 0)
        written = json.loads(baseline_path.read_text(encoding="utf-8"))
        check("and freezes its view as ready", "src/views/SettleView.vue" in written["ready"], True)
        check("but not the page, in either list",
              "src/views/Settle.vue" in written["ready"] + written["debt"], False)
        check("and the result passes", run_cli(root, baseline_path).returncode, 0)

        _fixture(root, {"frontend/src/router/index.ts": _router(settle), "frontend/src/views/SettleView.vue": (
            '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
            "<template><div /></template>\n"
        )})
        result = run_cli(root, baseline_path)
        check("a frozen view that starts fetching fails", result.returncode, 1)
        check("and is named as the regression", "src/views/SettleView.vue: A -> C" in result.stdout, True)
        baseline_path = _fixture_baseline(root)

        # the controls: an unused sibling, a type-only import, a view that fetches
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from './SettleView.vue'\n", ""
            ).replace('<SettleView :thing="thing" :id="route.params.id" />', "<div />"),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root, baseline_path)
        check("a view the page does not import does not make it a container", result.returncode, 1)
        check("so the page is reported as new debt", "src/views/Settle.vue: D" in result.stdout, True)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                "import SettleView from", "import type SettleView from"),
        })
        result = run_cli(root, baseline_path)
        check("a type-only import of the view does not pair it", result.returncode, 1)

        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": (
                '<script setup lang="ts">\nimport { api } from \'@/api\'\napi.get()\n</script>\n'
                "<template><div /></template>\n"
            ),
        })
        result = run_cli(root, baseline_path)
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
        result = run_cli(root, baseline_path)
        check("an imported view the page never renders does not pair", result.returncode, 1)
        check("and the shell page is named", "src/views/Settle.vue: D" in result.stdout, True)
        check("and no container is claimed", "container of" in run_cli(
            root, baseline_path, "--list").stdout, False)

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
        result = run_cli(root, baseline_path)
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
        result = run_cli(root, baseline_path)
        check("a container whose other rendered child is grade A passes", result.returncode, 0)
        check("and is listed as a container",
              "src/views/Settle.vue  (container of src/views/SettleView.vue)"
              in run_cli(root, baseline_path, "--list").stdout, True)

        # a view rendered only through <component :is> cannot be verified, and
        # a verifiable shape is the price of the exemption
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<component :is="SettleView" :thing="thing" :id="route.params.id" />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root, baseline_path)
        check("a view rendered only through <component :is> does not pair", result.returncode, 1)

        # a frozen page shedding its freeze into a shell: it imports the view,
        # never renders it, and --update would otherwise launder the migration
        ready_path = _fixture_baseline(root, {
            "ready": FIXTURE_BASELINE["ready"] + ["src/views/Settle.vue"]})
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": (
                "<script setup lang=\"ts\">\ndefineProps<{ n: number }>()\n</script>\n"
                "<template><div>{{ n }}</div></template>\n"
            ),
            "frontend/src/views/SettleView.vue": "",
        })
        check("a frozen page as plain A still passes", run_cli(root, ready_path).returncode, 0)
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />', "<div />"),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root, ready_path)
        check("a frozen page that shells out a view it never renders fails",
              result.returncode, 1)
        check("and is named as the regression", "src/views/Settle.vue: A -> D" in result.stdout, True)
        before = ready_path.read_text(encoding="utf-8")
        result = run_cli(root, ready_path, "--update")
        check("and --update refuses to lift the freeze", result.returncode, 1)
        check("leaving the baseline alone", ready_path.read_text(encoding="utf-8"), before)

        # the control: really moving the rendering DOES move the freeze
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container,
            "frontend/src/views/SettleView.vue": pure_view,
        })
        check("a frozen page that really moves its rendering passes",
              run_cli(root, ready_path).returncode, 0)
        result = run_cli(root, ready_path, "--update")
        check("and --update moves the freeze to the view", result.returncode, 0)
        written = json.loads(ready_path.read_text(encoding="utf-8"))
        check("the view is frozen now", "src/views/SettleView.vue" in written["ready"], True)
        check("and the page is out of both lists",
              "src/views/Settle.vue" in written["ready"] + written["debt"], False)

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
        result = run_cli(root, baseline_path)
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
        _fixture(root)
        baseline_path = _fixture_baseline(root)

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
        result = run_cli(root, baseline_path)
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
        result = run_cli(root, baseline_path)
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
              run_cli(root, baseline_path).returncode, 0)

        # a V-prefix is not a Vuetify registration: a globally registered
        # component the check cannot see must not be trusted
        _fixture(root, {
            "frontend/src/router/index.ts": _router(settle),
            "frontend/src/views/Settle.vue": container.replace(
                '<SettleView :thing="thing" :id="route.params.id" />',
                '<SettleView :thing="thing" :id="route.params.id" /><VReport />'),
            "frontend/src/views/SettleView.vue": pure_view,
        })
        result = run_cli(root, baseline_path)
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
        check("a real Vuetify tag still pairs", run_cli(root, baseline_path).returncode, 0)

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
        result = run_cli(root, baseline_path)
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
        check("the builtin RouterView still pairs", run_cli(root, baseline_path).returncode, 0)

        for rel in (
            "frontend/src/views/Settle.vue",
            "frontend/src/views/SettleView.vue",
            "frontend/src/components/PlainNote.vue",
            "frontend/src/components/ChildFetch.vue",
            "frontend/src/components/LocalFetch.vue",
        ):
            (root / rel).unlink(missing_ok=True)
        _fixture(root)
        baseline_path = _fixture_baseline(root)

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

        # -- 9. a wrong root is a 2, never a pass ---------------------------
        #    The trap this gate exists for: `frontend/` instead of the repo
        #    root has no `frontend/src` underneath, and an empty import graph
        #    would grade every component standalone — the silent-A failure.
        wrong = run_cli(root / "frontend", baseline_path)
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
        "a costumed panel — and four more at the extraction layer — a view in a "
        "comment, a child lost to a nested template, a V-prefixed stranger, a "
        "builtin shadowed by a local binding — with the controls that stay green, "
        "debt that is grandfathered, debt paid down, a type-only import that "
        "is not reach, --update refusing both edits, and four ways of not being able "
        "to judge, and a wrong root being a 2)"
    )
    return 0


# ---------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=str(REPO_ROOT), help="the tree to judge")
    parser.add_argument("--baseline", default=None, help=f"default: {DEFAULT_BASELINE}")
    parser.add_argument("--update", action="store_true", help="add ready scenes, drop paid debt")
    parser.add_argument("--list", action="store_true", help="print every scene with its grade")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    root = Path(args.root).resolve()
    baseline_path = Path(args.baseline) if args.baseline else root / DEFAULT_BASELINE
    try:
        return run(root, baseline_path, update=args.update, listing=args.list)
    except LookupError as exc:
        # The grader refuses to judge a root without `frontend/src` (an empty
        # import graph would grade everything standalone). Not a violation —
        # a 2, so a wrong root never looks like a pass.
        print(f"cannot judge: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
