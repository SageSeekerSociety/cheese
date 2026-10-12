# frontend/ — for agents working in this directory

Read [`../AGENTS.md`](../AGENTS.md) first; this file only adds what applies here.

## A component does not fetch

Nothing under `src/components/**` may import `@/api`, `@/services/*`,
`@/network/*` or `vue-router`. Data arrives as a prop or from a composable the
view called; `src/views/**` is where knowing that a server exists is allowed.
CI runs `pnpm run lint:boundary` (`lint:boundary:update` rewrites the baseline
downward); violations are frozen in `import-boundary-baseline.json`, so read the
current counts there. Reason, and the four component principles:
[`../.claude/rules/architecture.md`](../.claude/rules/architecture.md).

## When a component needs to know where it is

The rule above still holds — never `vue-router`, not even `import type`. Instead,
lightest first: `<NavLink :to="…">` (`components/common/NavLink.vue`),
`useNavigation()?.route` (`composables/useNavigation.ts`), or
`useNavigation()?.navigate(to)`; props and emits otherwise. Type a `to` as
`NavTarget` (`lib/navTarget.ts`), never `RouteLocationRaw` — why the two halves
of the boundary disagree about `import type` is in
`scripts/import-boundary-ratchet-core.mjs`.

## The component preview site

`/demo/catalog` renders every component in `views/demo/catalog.ts` — one page per
component, one stage per state, no backend/login/environment. To add one:
`node scripts/catalog-scaffold.mjs <src/...vue>` prints the entry skeleton (every
sentence a person owes is `TODO(catalog)`); paste it into a `catalog*.ts` volume
that `catalog.ts` imports and spreads; run
`pnpm exec vitest run src/views/demo/catalog.spec.ts`; then
`pnpm run lint:catalog:update`. Steps in Chinese:
[`../docs/manual/dev/frontend.md#catalog`](../docs/manual/dev/frontend.md#catalog).

**Every grade-A component must be catalogued or pending.** `pnpm run lint:catalog`
fails on a grade-A component in neither `catalog.ts` and its volumes nor the
`pending` list in `catalog-baseline.json`, and that list may only shrink. A
layout or a shell is not a candidate.

## A new scene runs standalone from day one

A **scene** is a router page under `src/views` or an SFC under
`src/components/panels`. `pnpm run lint:scenes` freezes the standalone-ready ones
in `scene-baseline.json`; a frozen scene that stops being ready fails, and a
**new** page or panel that is not ready fails from day one, with no baseline edit
to excuse it. Standalone-ready means grade A — props and events only, no
fetching, no `useRoute`/`$route`, no business store. To get there, lift the read
into a composable or the shell: `PanelDoc` (shell, fetches) →
`composables/usePanelDoc.ts` → `PanelDocView.vue` (props only). Full reasoning,
counts and the per-page table:
[`../docs/manual/dev/scenes.md`](../docs/manual/dev/scenes.md).

## Existing scene debt only loses dependencies

`pnpm run lint:scene-debt` freezes three exact sets in `scene-debt-baseline.json`
— non-A child edges, `useRoute` calls, legacy `src/network` imports — and
`lint:scene-debt:update` only removes; CI also compares the baseline to the merge
base, so a hand-added exemption fails too. Its failure output names each new
dependency and its repair; the rule lives in `.claude/scripts/scene-debt-ratchet.py`,
the grade it turns on in [`../docs/manual/dev/scenes.md`](../docs/manual/dev/scenes.md).

## Testing the rendered result

Keep template HTML comments in English: Vitest keeps them in the DOM, and
`src/views/spaces/spaces.i18n.spec.ts` checks `innerHTML` for Chinese without
stripping comments.

DOM tests (`happy-dom`) have no layout: interaction, layout, scrolling and
streaming changes need a real browser, and only the repo-root
`e2e/tests/layout-invariants.spec.ts` catches layout. A small Vite entry
mounting the real component with the repo's Vuetify plugin is enough when the
full catalog is impractical; for an intercepted-module A/B repro, verify the
replacement actually matched before trusting equal results.

The per-trap notes live with the code and in the rules — `src/utils/parser.spec.ts`
(jsdom for DOMPurify), `src/components/room/room-row.css` (`content-visibility`
clips row popovers), `../desktop/AGENTS.md` (a browser repro misses
`desktopNative.ts`) — and in
[`../.claude/rules/frontend.md`](../.claude/rules/frontend.md).

## Diagnosing ratchets

After merging main, compare a failing scene's `ready`/`debt` membership on both
sides, not only the grade error, and never move it back to debt or weaken the
grader to go green. The type ratchet reports counts (its JSON too); to see
diagnostics for a small change, use a disposable tsconfig under `frontend/` that
extends `tsconfig.json` and keeps ambient types such as `vite/client`, run
`tsc --noEmit -p <config>` (or `vue-tsc`), then delete it — diagnosis, not proof.
Read baseline counts from their JSON, not a prose snapshot; when lowering a
shared baseline, check prose restatements against the merged baseline too.
Metric-page merge handling:
[`metric-snapshots.md`](../.claude/rules/metric-snapshots.md).

## Caps and conventions

- Files under `src/` over **1000 lines** may not grow.
  `.claude/scripts/check-file-sizes.py` judges each changed file against its size
  at the merge base with `origin/main`.
- `pnpm run lint` is the read-only ESLint (`lint:fix` writes); never gate on the
  writing form. Design tokens and the two themes:
  [`../.claude/rules/frontend.md`](../.claude/rules/frontend.md).
- From the repository root, `task ci:fast` before you push runs the checks your
  changed paths select; `task fe:check` runs the whole frontend check.
- A dev server may already be running in this worktree; do not restart one you
  did not start.
- A new page under `src/views/admin/features/`: the chart components take props
  and the view is the only thing that fetches —
  [`../docs/manual/dev/feature-stats.md`](../docs/manual/dev/feature-stats.md).
