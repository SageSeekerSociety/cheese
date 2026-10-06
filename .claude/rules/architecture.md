---
paths:
  - "backend/app/**"
  - "frontend/src/**"
---

# Module boundaries, and which of them a machine actually checks

Four checks run on every commit and in CI. Everything below says *why* each
rule exists and what enforces it, so that a rule nobody checks is not mistaken
for one that is. Rules marked **建议** are conventions: no tool will stop you.

One command runs all four: `task boundaries`. Individually:

| Check | Command |
|---|---|
| Backend import graph | `cd backend && uv run python scripts/check_boundaries.py` |
| Component imports | `pnpm --dir frontend run lint:boundary` |
| File sizes | `python3 .claude/scripts/check-file-sizes.py` |
| Scenes run standalone | `pnpm --dir frontend run lint:scenes` |

All four print their baseline and their refresh command when they fail, and all
three carry `--self-test` (also run in CI — a check nobody has watched fail is
not a check).

What they cannot say is whether the tree is getting *better*: each one is a
ratchet against a frozen baseline, and a ratchet that holds still reports success
forever. That half is a board, not a gate — `python3 .claude/scripts/arch-metrics.py`
(and `--compare <ref>` for two revisions side by side), reported on main and
weekly by `.github/workflows/arch-metrics.yml`, never able to fail a run. What it
measures, how to read it, and today's numbers: `docs/manual/dev/arch-metrics.md`
(发布在文档站的「架构指标」一页).

## Backend: the import graph is checked, not assumed

`backend/.importlinter` declares three contracts (import-linter, AST-based, so
an import inside a function counts too — of the 32 import statements behind the
26 frozen layer violations, exactly one is at module level, and the rest are
inside functions, which is how an import that "would never happen" happens):

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
  `importer -> imported` pair — 26 + 56 + 178 = 260 today. New ones fail CI.
- No wildcards. `check_boundaries.py` fails (exit 1) on any `*` in the freeze,
  because an exemption that can absorb a file nobody looked at is not an
  exemption.
- Shrinking is free and needs no edit: a frozen pair that no longer exists is a
  warning, never a failure. Run
  `uv run python scripts/boundary_baseline.py --update` to drop the stale lines
  (it refuses to *add* any without `--freeze-new`, so accepting new debt is a
  visible act).
- Exit 2 means "could not judge" (a missing `app` on the path, an unparseable
  config, a contract that stopped running) and is never a pass.

**建议** A module-level mutable singleton — a shared client, engine or cache
built at import time — is a boundary leak that no contract here sees. If one is
unavoidable it belongs in `app.core` with a comment saying what owns its
lifetime; new ones are an admission, not a habit.

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
components when it landed, 59 in 47 today (`frontend/import-boundary-baseline.json`);
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

Missing `/demo/catalog` entries are reported as a warning count, never a failure:
what belongs in the preview site is a product decision, and
`pnpm exec vitest run src/views/demo/catalog.spec.ts` is the mechanical claim
that a catalogued component really does render alone.

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
