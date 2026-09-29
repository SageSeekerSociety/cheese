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

91 violations across 57 components are frozen in
`import-boundary-baseline.json`; only new ones fail. It is a separate ESLint
config (`eslint.boundary.config.mjs`) rather than a rule in `eslint.config.mjs`
for exactly that reason — as a plain rule it reddens the whole tree on day one.
Reasoning and the four component principles:
[`../.claude/rules/architecture.md`](../.claude/rules/architecture.md).

## Caps and conventions

- Files under `src/` over **1000 lines** may not grow; 19 are already there and
  are frozen at their current size. `.claude/scripts/check-file-sizes.py` judges
  only files that differ from `origin/main`.
- `pnpm run lint` is the read-only ESLint (the writer is `lint:fix`); never gate
  on the writing form. Design tokens and the two themes have their own ratchet —
  [`../.claude/rules/frontend.md`](../.claude/rules/frontend.md).
- `task fe:check` runs lint, boundaries, style, typecheck, unit tests and build.
- A dev server may already be running on 3001/3002 in this worktree; do not
  restart one you did not start.
