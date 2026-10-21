# backend/ — for agents working in this directory

Read [`../AGENTS.md`](../AGENTS.md) first; this file only adds what applies here.

## The import graph is checked, and a wrong import fails CI

`app/` is layered **api -> domain -> core**. Imports that point backwards, a
route reaching into another domain's models, an import pointing up the seven
layers of `app.domain` (every child of it belongs to one layer; a new package
or top-level module is a violation until the layer table names it), a cycle
between sibling domains under `app.domain`, or one route module importing
another (directly or through a helper) are failures — including when the
import sits inside a function. The contracts are declared in `.importlinter`,
and what the check holds open is measured from the tree at the merge base with
`origin/main`: a violation that tree already has is not this branch's to fix,
one only this branch has is. Shared request dependencies (`DbSession`,
`AuthUser`) live in
`app/api/request_deps.py`, so a route never needs another route's import;
`task boundaries` (`Taskfile.yml`) runs them.

An import inside a function body says why it cannot be at the top of the
module, with `# deferred-import: <reason>` on the same line or the line above.

## Refactors must preserve the callers and the frozen graph

When shrinking an oversized module, extract new leaf modules rather than moving
existing modules just to lower the line count. The import ledgers key debt by
module path; relocating it is not removing it, and accepting
new frozen pairs is not a routine refactor step.

Before moving a public name in `app/`, search the whole repository for callers,
including top-level `scripts/`, not just `backend/`. Pyright's configured scope
is `app`, so a green backend type check cannot clear those outside callers.

## Generated routes and database predicates

The route index is not committed. `scripts/route_index.py --emit` prints it to
stdout, and it is generated where it is judged — the frontend `check` job and
this suite — so there is no file to regenerate and no conflict to
resolve in it. After adding, removing or reordering routes, from `backend/` run

- `uv run python -m scripts.route_index --check` — no later route is provably
  shadowed by an earlier one. A shadow is fixed by reordering or narrowing the
  earlier route; nothing exempts one.

Keep a partial index's predicate and its querying expression aligned, including
literal values and casts; a parameterised or differently spelled expression may
not prove the index applicable. For the handle-prefix predicates, reuse
`app/domain/block/indexed_rows.py`'s literal `^@` expression: the asyncpg DDL path
can leave SQLAlchemy's escaped `%%` in a LIKE predicate.

Tests have additional setup rules in [`tests/AGENTS.md`](tests/AGENTS.md).
Changes to `app/domain/usage/subscription_ingest.py` also follow the normal-path
and cache-pricing constraints in
[`../deploy/metering-proxy/AGENTS.md`](../deploy/metering-proxy/AGENTS.md).

## Caps and conventions

- The pull request's CI is the verdict (see [`../AGENTS.md`](../AGENTS.md));
  locally, run the test files your change touches. `task be:check` runs the
  whole backend check on a machine that can hold it.
- Migrations: [`../.claude/rules/migrations.md`](../.claude/rules/migrations.md)
  — the alembic chain has exactly one head at all times.
- Test pitfalls by symptom:
  [`../.claude/rules/backend-tests.md`](../.claude/rules/backend-tests.md).
- This worktree may already have a uvicorn on port 8081; `task dev` starts
  another. Do not restart a server you did not start.
- A new page under `app/domain/feature_stats/`: register it once, read only
  tables the feature already writes, and keep the questioner away from the raw
  question — [`../docs/manual/dev/feature-stats.md`](../docs/manual/dev/feature-stats.md).
