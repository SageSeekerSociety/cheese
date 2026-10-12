---
paths:
  - "backend/app/**"
  - "frontend/src/**"
---

# Module boundaries: the conventions no check can hold

The boundary checks run in CI on matching commits, and locally as one command,
`task boundaries` (`Taskfile.yml`), which dispatches to `backend/Taskfile.yml`
and `frontend/Taskfile.yml`. Each check's own file and failure output say what
it enforces and how to fix a failure, so this file keeps only (a) the
conventions no machine holds and (b) the landing points other files point at.

A check cannot say whether the tree is getting *better*: a ratchet against a
frozen baseline holds still and reports success forever. That half is the board,
`arch-metrics.py`, which reports and never fails. Counts, history and
per-contract reasoning: `docs/manual/dev/architecture.md`,
`docs/manual/dev/arch-metrics.md`, `backend/.importlinter`.

## Backend: the import graph is checked, not assumed

The import graph and the contracts that describe it — `api -> domain -> core`,
sibling domains acyclic, route modules independent of one another, and the
`turn` layering — are declared in
`backend/.importlinter` (AST-based, so an import inside a function counts too).
`check_boundaries.py` names the fix contract by contract and what the freeze
permits (no wildcards; exit 2 is "could not judge", never a pass).

Conventions no contract sees:

- **A module-level mutable singleton** — a shared client, engine or cache built
  at import time — is a boundary leak no contract sees. If one is unavoidable it
  belongs in `app.core` with a comment saying what owns its lifetime; a new one
  is an admission, not a habit.
- **Boundaries follow real dependencies, not a directory table.** The contracts
  name packages the code actually imports, and the frozen list is generated from
  the graph rather than transcribed by hand.
- **A deferred import's reason names the cycle** it breaks (or an optional
  dependency, a start-up cost, a test double). "Avoid circular import" with no
  cycle named is not a reason — check whether the cycle still exists first.
  `check_deferred_imports.py` asks for a reason; only a reader can judge it.

## Frontend: a component does not fetch

Enforced by ESLint under `src/components/**` (no `@/api`, `@/services/*`,
`@/network/*`, `vue-router`); the rule's own message is the fix. The four
principles, in the order they matter:

1. **Data in by prop, out by emit.** A component that takes what it renders
   can be reasoned about from its template; **the shape of the props is on
   you**.
2. No fetching from the network layer — `pnpm run lint:boundary`.
3. No route knowledge — the same rule.
4. **Testable without a server**. No check decides which side of that line a
   file is on: a file that needs a server to render is a view or a container,
   not a component.

## Frontend: one API module per domain behind `src/api.ts`

`frontend/src/api.ts` holds nothing but `export … from` statements
(`frontend/eslint.config.mjs` enforces that); the code lives in
`frontend/src/api/<domain>.ts`, and importers keep writing `@/api`.

**A module under `src/api/` imports `./http` and its siblings, not `../api`.**
Going through the facade puts the call inside whatever a test mocked `@/api`
with. Some older modules still do, and changing one changes what its tests
intercept, so it is its own change.

## Files have a size cap

Judged against the merge base. Split the file: `check-file-sizes.py`'s failure
names the cap and its reason, and `EXEMPT` is the one considered exception. A
file over its cap is a prompt to split, not a defect; raising the cap helps
nothing.

Scenes and the catalog live in `frontend/AGENTS.md` and
`docs/manual/dev/scenes.md`.
