---
paths:
  - "backend/app/**"
  - "frontend/src/**"
---

# Module boundaries: the conventions no check can hold

Seven checks gate a change (`task boundaries`; the commands are in `Taskfile.yml`).
Each check's own file and failure output say what it enforces and how to fix a
failure, so this file keeps only (a) the conventions no machine holds and (b) the
landing points other files point at.

A check cannot say whether the tree is getting *better*: a ratchet against a
frozen baseline holds still and reports success forever. That half is the board,
`arch-metrics.py`, which reports and never fails. Counts, history and
per-contract reasoning: `docs/manual/dev/architecture.md`,
`docs/manual/dev/arch-metrics.md`, `backend/.importlinter`.

## Backend: the import graph is checked, not assumed

`api -> domain -> core`, sibling domains acyclic, and the four `turn` contracts
are declared in `backend/.importlinter` (AST-based, so an import inside a
function counts too). `check_boundaries.py` names the fix contract by contract
and what the freeze permits (no wildcards; exit 2 is "could not judge", never a
pass); the commands are in `backend/AGENTS.md`.

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

## Files have a size cap

`frontend/src` 1000 lines (`src/api/` 400), `backend/app` 1500, judged against
the merge base. Split the file: `check-file-sizes.py`'s failure names the cap and
its reason, and `EXEMPT` is the one considered exception. A file over its cap is
a prompt to split, not a defect; raising the cap helps nothing.

Scenes and the catalog live in `frontend/AGENTS.md` and
`docs/manual/dev/scenes.md`; `src/api.ts` re-exports only, which
`frontend/eslint.config.mjs` enforces.
