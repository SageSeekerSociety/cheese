# frontend/ — for agents working in this directory

Read [`../CLAUDE.md`](../CLAUDE.md) first; this file only adds what applies here.

## A component does not fetch

Nothing under `src/components/**` may import `@/api`, `@/services/*`,
`@/network/*` or `vue-router`. Data arrives as a prop or from a composable the
view called; `src/views/**` is where knowing that a server exists is allowed.
This is checked on every commit and in CI:

```bash
pnpm run lint:boundary          # what CI runs
pnpm run lint:boundary:update   # after you fix some, rewrite the baseline
```

66 violations across 54 components are frozen in
`import-boundary-baseline.json`; only new ones fail. It is a separate ESLint
config (`eslint.boundary.config.mjs`) rather than a rule in `eslint.config.mjs`
for exactly that reason — as a plain rule it reddens the whole tree on day one.
Reasoning and the four component principles:
[`../.claude/rules/architecture.md`](../.claude/rules/architecture.md).

## When a component needs to know where it is

The rule above still holds — a component does not import `vue-router`. It has
to do one of three things instead, lightest first:

| The component | Use |
|---|---|
| is itself a destination (a row, a card, a tab) | `<NavLink :to="…">` — `components/common/NavLink.vue` |
| only draws where you are (which tab is current) | `useNavigation()?.route` — `composables/useNavigation.ts` |
| has to go somewhere on a click with no parent to ask | `useNavigation()?.navigate(to)` |

**Props and emits are still the default answer.** If a parent knows the
destination, take `to` as a prop and emit the click (`UserRef` does this) —
the composable is for the components that have no parent to ask.

The seam reads `$router`/`$route` off `app.config.globalProperties` (what
`app.use(router)` installs) and returns `null` when there is no router, so the
same component renders without one — minus the destination, never with a
warning. `NavLink` renders a real `<a>` with `href` when a router is present
and the same `<a>` without one when it is not: a thing that looks clickable and
does nothing is worse than no thing at all.

Type a `to` prop as `NavTarget` from `lib/navTarget.ts`, not
`RouteLocationRaw` from `vue-router` — an `import type` from `vue-router`
counts as a boundary violation just like a value import. The API layer's half
is looser: `import type { User } from '@/network/api/users/types'` is left
alone, because a type binding is erased before anything runs and so cannot
fetch. Only the vue-router half has an alternative to point you at.

## The component preview site

`/demo/catalog` renders every component in `views/demo/catalog.ts`, one page
per component, one stage per state — with no backend, no login and no
environment:

```bash
pnpm dev            # then open /demo/catalog on the port it prints
```

To add a component:

1. Add an entry to `views/demo/catalog.ts`: `id`, `title`, `about`, `file`,
   the `component`, the plugins it needs (`needs`), and one `states` item per
   state worth seeing. Name the state, say what it shows, pass the props, and
   give the text that proves it rendered.
2. Build those props in `views/demo/catalogFixtures.ts` out of the product's
   own demo scenes where you can — a card in the catalog should be a card from
   a real room, not a hand-typed object that drifts.
3. `pnpm exec vitest run src/views/demo/catalog.spec.ts`. The spec walks the
   registry and mounts every state with only the plugins that state declares,
   failing on any console warning or error — so a `needs` that is too small is
   a red test, not a surprise in the browser.

Rendering the site writes nothing to the source tree.

## A new scene runs standalone from day one

A **scene** is a router page under `src/views` or an SFC under
`src/components/panels`. The ones that render with props alone today are frozen
in `scene-baseline.json`; the ratchet only has one direction:

```bash
pnpm run lint:scenes           # what CI runs
pnpm run lint:scenes:update    # after a scene becomes standalone-ready
```

- A frozen scene that stops being standalone-ready **fails** the check.
- A **new** page (a route added to `src/router/`) or a **new** panel that is not
  standalone-ready **fails** the check — from day one, with no baseline edit that
  can excuse it.
- Pages and panels that were already not standalone-ready are listed as debt and
  are allowed to stay there.
- `--update` only moves a scene from debt to ready; it refuses to write anything
  if a frozen scene fell off or a new one is not ready.

Standalone-ready means A on the metrics board: props and events only, no
fetching, no `useRoute`/`$route`, no business store (the app-chrome stores
`usePageTitleStore`/`useNavigationStore` are B and do not count). How to get
there: lift the fetching into a composable or the shell and pass the result down
— the worked example is `PanelDoc` (shell, fetches) → `composables/usePanelDoc.ts`
→ `PanelDocView.vue` (props only). Full reasoning, the current counts and the
per-page table: [`../docs/manual/dev/scenes.md`](../docs/manual/dev/scenes.md).

A standalone-ready scene that is not in `views/demo/catalog.ts` is reported as a
warning, not a failure — read the warning count as a to-do list, and the three
steps above as the fix.

## Caps and conventions

- Files under `src/` over **1000 lines** may not grow; 19 are already there and
  are frozen at their current size. `.claude/scripts/check-file-sizes.py` judges
  only the files this branch changed, each against the size it had at the merge
  base with `origin/main`.
- `pnpm run lint` is the read-only ESLint (the writer is `lint:fix`); never gate
  on the writing form. Design tokens and the two themes have their own ratchet —
  [`../.claude/rules/frontend.md`](../.claude/rules/frontend.md).
- `task fe:check` runs lint, boundaries, scenes, style, typecheck, unit tests and build.
- A dev server may already be running on 3001/3002 in this worktree; do not
  restart one you did not start.
- A new page under `src/views/admin/features/`: the chart components take props
  and the view is the only thing that fetches —
  [`../docs/manual/dev/feature-stats.md`](../docs/manual/dev/feature-stats.md).
