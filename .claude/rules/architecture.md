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
frozen baseline holds still and reports success forever. `arch-metrics.py`
reports the measurements and never gates a change. Counts, history and
per-contract reasoning: `docs/manual/dev/architecture.md`,
`docs/manual/dev/arch-metrics.md`, `backend/.importlinter`.

## Backend: the import graph is checked, not assumed

The import graph and the contracts that describe it — `api -> domain -> core`,
sibling domains acyclic, `app/domain`'s seven layers (dependencies point down,
siblings may import each other, every child of `app.domain` must sit in exactly
one layer), route modules independent of one another, and the `turn` layering —
are declared in `backend/.importlinter` (AST-based, so an import inside a
function counts too). A new child of `app.domain` — a package or a top-level
module — is a contract violation until the layer table names it.
`check_boundaries.py` names the fix contract by contract (no wildcards; exit 2 is
"could not judge", never a pass). What it judges this tree against is the tree at
the merge base with `origin/main`, graded with the same contracts: the
violations that tree already has are held open, and a violation only this tree
has is the verdict. Nothing about that baseline is stored, so no branch carries
a list of exemptions; the file's own `ignore_imports` lines are for a violation
somebody decided to accept, and are meant to stay rare and short.

Conventions no contract sees:

- **A module-level mutable singleton** — a shared client, engine or cache built
  at import time — is a boundary leak no contract sees. If one is unavoidable it
  belongs in `app.core` with a comment saying what owns its lifetime; a new one
  is an admission, not a habit.
- **Boundaries follow real dependencies, not a directory table.** The contracts
  name packages the code actually imports, and the baseline is measured from the
  same graph rather than transcribed by hand.
- **A deferred import's reason names the cycle** it breaks (or an optional
  dependency, a start-up cost, a test double). "Avoid circular import" with no
  cycle named is not a reason — check whether the cycle still exists first.
  No check asks for the reason; a reader does.

## Backend: what the platform pays for is metered, never called straight

A model call or a web search the platform pays for goes out on the payer's key
through the platform's metering. From backend code that is the gateway
(LiteLLM) on the payer's virtual key — the project's, the task's, a person's
own — which admits it against that key's budget, prices it, and leaves its
spend to be drained into credits (`docs/manual/dev/gateway.md`); from an
agent's session it is the metering proxy, which alone holds the platform's
Claude subscription credential (`docs/manual/dev/metering-proxy.md`). Backend
code never calls a model or search provider directly, and never hands a
session a provider credential the platform pays for: such a call is priced by
nobody, charged to nobody and outside every budget. The only direct calls are
on someone's own account — a member's or team's own API key or model service
(`backend/app/domain/agent_instance/account_connections.py`) — which they pay
for.

## Frontend: a component does not fetch

Enforced by ESLint under `src/components/**` (no `@/api`, `@/services/*`,
`vue-router`); the rule's own message is the fix. Outside the component
boundary the whole tree is barred from `@/network` — that stack was retired and
`frontend/eslint.config.mjs` keeps a `no-restricted-imports` rule so it cannot
come back. The four principles, in the order they matter:

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

Scenes and the catalog live in `frontend/AGENTS.md` and
`docs/manual/dev/scenes.md`.
