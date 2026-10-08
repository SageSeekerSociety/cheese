---
paths:
  - "backend/app/**"
  - "frontend/src/**"
---

# Module boundaries, and which of them a machine actually checks

Five checks run on every commit and in CI. Everything below says *why* each
rule exists and what enforces it, so that a rule nobody checks is not mistaken
for one that is. Rules marked **建议** are conventions: no tool will stop you.

One command runs all five: `task boundaries`. Individually:

| Check | Command |
|---|---|
| Backend import graph | `cd backend && uv run python scripts/check_boundaries.py` |
| Backend imports inside a function | `cd backend && uv run python scripts/check_deferred_imports.py` |
| Component imports | `pnpm --dir frontend run lint:boundary` |
| File sizes | `python3 .claude/scripts/check-file-sizes.py` |
| Scenes run standalone | `pnpm --dir frontend run lint:scenes` |
| Scene child/route debt and old network importers only shrink | `pnpm --dir frontend run lint:scene-debt` |
| Grade-A components are in the catalog | `pnpm --dir frontend run lint:catalog` |

All five print their baseline and their refresh command when they fail, and each
has tests of its own that CI runs — a check nobody has watched fail is not a
check: four carry `--self-test` (boundaries in test.yml, file sizes, scenes and
catalog in repo-guards.yml), and the component-import ratchet is covered by
`frontend/scripts/import-boundary-ratchet.test.mjs` under `pnpm run test:ratchet`.

What they cannot say is whether the tree is getting *better*: each one is a
ratchet against a frozen baseline, and a ratchet that holds still reports success
forever. That half is a board, not a gate — `python3 .claude/scripts/arch-metrics.py`
(and `--compare <ref>` for two revisions side by side), reported on main and
weekly by `.github/workflows/arch-metrics.yml`, never able to fail a run. What it
measures, how to read it, and today's numbers: `docs/manual/dev/arch-metrics.md`
(发布在文档站的「架构指标」一页).

## Backend: the import graph is checked, not assumed

`backend/.importlinter` declares three contracts (import-linter, AST-based, so
an import inside a function counts too — of the 30 import statements behind the
27 frozen layer violations, one is at module level under
`if TYPE_CHECKING:`, and the other 29 are inside functions, which is how an import
that "would never happen" happens):

- **api → domain → core, never backwards.** A route may not reach into a
  domain's internals, and nothing below `app.api` may import `app.api`.
- **API routes do not touch another domain's models or repositories.** The
  route calls that domain's service. (Repositories *inside* domain code are
  already ratcheted by `backend/tests/unit/test_domain_import_guard.py`, which
  holds a two-way ledger; this contract deliberately covers what that one does
  not, so one new violation is not reported twice. `app.api.deps` is listed with
  `models` because it is the graph's other way in.)
- **Sibling domains under `app.domain` are acyclic.** `project` and `agent`
  may depend on each other only through a declared seam; a cycle means neither
  can be understood or tested alone.

Boundaries follow **real dependencies, not a directory table**: the contracts
name packages the code actually imports, and the frozen list is generated from
the graph rather than transcribed by hand.

Freeze policy, which is the whole ratchet:

- Every current violation is frozen in the same file as an exact
  `importer -> imported` pair — 27 + 49 + 153 = 229 today (C1 + C2 + C3). New
  ones fail CI.
- No wildcards. `check_boundaries.py` fails (exit 1) on any `*` in the freeze,
  because an exemption that can absorb a file nobody looked at is not an
  exemption.
- Shrinking is free and needs no edit: a frozen pair that no longer exists is a
  warning, never a failure. Run
  `uv run python scripts/boundary_baseline.py --update` to drop the stale lines
  (it refuses to *add* any without `--freeze-new`, so accepting new debt is a
  visible act). C3's frozen list is a cycle-breaker set, and that set is not
  unique: recomputed from scratch it can come out as a different set of the
  same knot, which `--update` reports as new violations and refuses. When that
  happens, delete exactly the lines `lint-imports` lists under "No matches for
  ignored import" instead of freezing a reshuffled set.
- Exit 2 means "could not judge" (a missing `app` on the path, an unparseable
  config, a contract that stopped running) and is never a pass.

**建议** A module-level mutable singleton — a shared client, engine or cache
built at import time — is a boundary leak that no contract here sees. If one is
unavoidable it belongs in `app.core` with a comment saying what owns its
lifetime; new ones are an admission, not a habit.

## Backend: an import inside a function says why, or goes to the top

An `import` inside a function body is invisible to whoever reads the top of the
module, and to every tool that reads dependencies from module level —
import-linter sees it, almost nothing else does. It is also the usual way a
cycle is dodged instead of removed: 26 of the 27 frozen layer violations above
are imports inside a function. So such an import either moves to the top of the
module, or says why it cannot, on the same line or the line directly above:

```python
# deferred-import: breaks the cycle domain.topic -> domain.project
from app.domain.project.services import ProjectService
```

Good reasons are specific: the cycle it breaks, an optional dependency that may
be absent, a start-up cost worth deferring, a test double that replaces the name
on its home module (often better solved by importing the module and calling
`module.name(...)`, which keeps the import at the top and the patch working).
"Avoid circular import" with no cycle named is not one — check whether the cycle
still exists before writing it.

**Enforced** by `backend/scripts/check_deferred_imports.py` (pre-commit hook
`deferred-imports`, CI, `task boundaries`):

- Unannotated ones are frozen **per file** in `backend/deferred-import-baseline.json`.
  A file not in it must have none; a file in it may only go down. 773 in 171
  files when the ratchet started (2026-10, after the sign-in routes were lifted
  from 850).
- Annotated ones are not limited, and every run lists them with their reasons,
  so a reviewer sees what was explained and how.
- Counting is by AST: an import lexically inside a `def`/`async def`, once each
  however deeply nested. A module-level `if TYPE_CHECKING:` import is not inside
  a function and does not count. `arch-metrics.py`'s `deferred_imports` (the
  board's number, annotated or not) imports the same counter, so the two cannot
  drift apart.
- Paying down is free: a baseline above the tree is a warning. Lower it with
  `uv run python scripts/check_deferred_imports.py --update`, which only
  shrinks; growth is refused unless you add `--freeze-new`, which prints every
  line it freezes. `--self-test` (also in CI) proves the check still goes red.
- Exit 0 pass, 1 a file grew, 2 could not judge (no baseline, an unreadable
  baseline, a file that does not parse) — never a pass.

## Frontend: a component does not fetch

Enforced by ESLint under `frontend/src/components/**`: no `src/api.ts`, no
`src/services/**`, no `src/network/**`, no `vue-router`. The rule judges **where
the import resolves, not how it is spelled** — `@/api`, `../api` and
`../../api` are the same dependency and the same violation, while
`src/components/chat/services/*` reached as `./services/x` is not the API layer
and stays legal. `frontend/src/views/**` is unrestricted — a view is the thing
allowed to know that a server exists.

Four principles, in the order they matter:

1. **Data in by prop, out by emit.** A component that takes what it renders can
   be reasoned about from its template. **Enforced** only where the prop is a
   fetch; the shape of the props is on you.
2. **No fetching.** The data arrives as a prop or from a composable the view
   called. **Enforced** by `pnpm run lint:boundary`.
3. **No route knowledge.** `useRoute`/`useRouter` inside a component couples it
   to one page in a way no test can see. **Enforced** by the same rule.
4. **Testable without a server.** If a component cannot be rendered with props
   alone, it is a view or a container, not a component. **建议** — no check can
   decide which side of that line a file is on.

The rule is ratcheted because the tree started dirty — 127 violations in 82
components when it landed, 21 in 21 on 2026-10-07 (`frontend/import-boundary-baseline.json`);
a gate that reddened the whole tree on day one would be switched off within a week. It is a separate ESLint config
(`eslint.boundary.config.mjs`) rather than a rule in `eslint.config.mjs` for the
same reason. Only *new* violations fail; `pnpm run lint:boundary:update` writes
the baseline after you fix some. (The 83 → 127 jump is the resolved-path rule
seeing the 44 relative-path imports the glob-based one could not, not new debt.
`lint:boundary:update` only ever lowers a frozen count, so a rule change that
*adds* violations rebuilds the baseline from zero — see the commit that fixed
it. The 95 → 74 drop is the API-layer half ceasing to count type-only imports:
a type binding is erased before anything runs, so it cannot fetch. The 74 → 59
drop is fifteen components leaving the baseline: eight taking their route
through `useNavigation`, seven panel components that came out of making the
work panels standalone. The
vue-router half keeps counting type-only imports on purpose, because there the
alternative (`NavTarget` from `lib/navTarget.ts`) is what the rule points you at.)

## Frontend: one API module per domain behind `src/api.ts`

`frontend/src/api.ts` is a facade: it holds nothing but `export … from`
statements, and the code lives in `frontend/src/api/<domain>.ts` (feedback,
members, topics, `admin/gateway`, …). Importers keep writing `@/api` and tests
keep writing `vi.mock('@/api')`; the facade is what both resolve to.

- **A new endpoint goes in its domain module**, never in `api.ts`. **Enforced**
  by `no-restricted-syntax` on `src/api.ts` in `eslint.config.mjs`.
- **A module under `src/api/` stays under 400 lines.** Past that it is two
  domains. **Enforced** by `.claude/scripts/check-file-sizes.py`.
- **A module imports `./http` and its siblings, not `../api`.** Going through
  the facade puts a call inside whatever a test mocked `@/api` with. Sixteen
  older modules still do (`request` from `'../api'`); changing one changes what
  its tests intercept, so it is its own change. **建议**.
- Helpers shared by modules but not part of the public surface
  (`legacyRequest`, `feedbackQuery`) live in `api/legacy.ts` / `api/query.ts`,
  which the facade does not re-export.

## A scene runs standalone, and the set only grows

`docs/manual/dev/scenes.md` (发布在文档站的「场景清单」一页) grades every scene —
a router page under `frontend/src/views` or an SFC under
`frontend/src/components/panels` — by the same A/B/C/D rule as the metrics board
(standalone-ready = A: props and events only). What changed on 2026-09-30 is that
the grade became a ratchet rather than a report: `pnpm run lint:scenes` freezes
the scenes that are A today in `frontend/scene-baseline.json`, and a scene that
falls off, or a **new** scene that is not A, fails. Pre-existing non-A scenes are
listed in the same file as debt and are allowed to sit there; `--update` may add
to the ready set and subtract from the debt set and will refuse to do either
backwards.

A new page that has to read the address or fetch and save is not stuck: it may
be a **container** if it renders through a sibling `<Page>View.vue` it imports.
Importing is not enough: the page's template must actually render the view
(statically — a `<component :is>` page cannot be judged and is not a
container), and every other component the template renders must be verifiable
as standalone too, otherwise the fetching just moved one level down. The
reading is literal about what renders: comments do not, nested `<template>`
blocks do not end the scan, attribute quotes are honoured
(`title="</template>"` is a value, not a tag), a lowercase tag is native only
if it is a real HTML/SVG element, Vuetify is trusted by the repo's own
auto-import table and never by the V- prefix, and a local binding — import or
declaration — shadows a builtin name. The view
is then the scene — graded, frozen, required to be A — and the page
is judged as its container on every run, in neither list. That is the
`PanelDoc` → `usePanelDoc` → `PanelDocView` shape, named so the check can find
it: the rule is about the rendering being standalone, and a route has to get
its data somewhere. Pages only: a panel has no route, so a panel in the same
costume is a panel that fetches, not a container.

Two things about it are worth knowing from this file:

- The page list is derived from the router's import graph, so hanging a new route
  puts a new scene under the gate with no registry to update. The panel list is
  every SFC under `components/panels/`.
- The grader is one module, `.claude/scripts/frontend_grade.py`, imported by both
  this gate and `arch-metrics.py`. A definition of "standalone" that could drift
  between the board and the gate would be worth less than neither. Its rule that
  a type-only import is not reach (`import type` is erased at build time) is one
  half of the story; the other half is that a chain must be followed through
  `.vue` edges too, not only `.ts` ones — a page that renders a child which
  fetches needs the network as much as one that fetches itself. Only the first
  half was implemented at first, which graded ten such scenes A and froze them;
  both halves landed 2026-09-30.

Missing `/demo/catalog` entries are no longer a warning here. They are their own
ratchet, `pnpm run lint:catalog` (`.claude/scripts/catalog-ratchet.py`): every
grade-A `.vue` under `frontend/src` (the preview site's own `views/demo/`
excluded) is either catalogued — `views/demo/catalog.ts`, or a `catalog*.ts`
volume it imports and spreads (transitively; `*Fixtures.ts` not read),
imports the `.vue` and uses it as an entry's `component:`, comments not
counted — or listed as `pending` in
`frontend/catalog-baseline.json`, a list that may only shrink. A new or newly-A
component that is in neither fails; `--update` only crosses entries off.
`pnpm exec vitest run src/views/demo/catalog.spec.ts` remains the mechanical
claim that a catalogued component really does render alone.

## Files have a size cap, and cap it where it stands

`frontend/src` 1000 lines (`.vue`/`.ts`/`.js`), `backend/app` 1500 (`.py`).
A file does not become unreadable in one commit, so this is differential: only
the files this branch changed are read, each against the size it had where the
branch left `origin/main` (the merge base), a new file must land under the cap,
and a file already over it is frozen at its size there — 35 files are, so the
check fails a file that *grew*, not the ones that are big today. Shrinking is
always allowed. A checkout that cannot compute that merge base is a "cannot
judge" (exit 2), never a comparison against main's moving tip. The failure
message names the file, its line count, the cap and why the cap exists.

An exemption is one exact path and one reason, in `EXEMPT` in
`.claude/scripts/check-file-sizes.py` — a registry or a generated table
legitimately grows line by line, and a cap on it only teaches people to route
around the cap. Nothing is exempt today.

Over its cap, a file is a prompt to split, not a defect: the split is the point,
and raising the cap is the one answer that helps nothing.
