# backend/ — for agents working in this directory

Read [`../AGENTS.md`](../AGENTS.md) first; this file only adds what applies here.

## The import graph is checked, and a wrong import fails CI

`app/` is layered **api -> domain -> core**. Imports that point backwards, a
route reaching into another domain's models, or a cycle between sibling domains
under `app.domain` are failures — including when the import sits inside a
function. Pre-existing violations are frozen in `.importlinter` as exact
`importer -> imported` pairs; new ones are not.

```bash
uv run python scripts/check_boundaries.py            # what CI runs (add --self-test)
uv run lint-imports                                  # the same, unfiltered
uv run python scripts/boundary_baseline.py --update  # drop stale frozen lines (never adds)
```

There is no wildcard exemption: if a pair is in the freeze, it names that module
and that target. Full reasoning, and which rules are conventions rather than
checks: [`../.claude/rules/architecture.md`](../.claude/rules/architecture.md).

Imports inside a function body may only go down: lift one to the top of the
module, or say why it cannot be with `# deferred-import: <reason>` on the same
line or the line above. Unexplained ones are frozen per file in
`deferred-import-baseline.json`; a new file may have none.

```bash
uv run python scripts/check_deferred_imports.py            # what CI runs (add --self-test)
uv run python scripts/check_deferred_imports.py --update   # lower the baseline (never raises)
```

## Refactors must preserve the callers and the frozen graph

When shrinking an oversized module, extract new leaf modules rather than moving
existing modules just to lower the line count. The import and deferred-import
ledgers key debt by module path; relocating it is not removing it, and accepting
new frozen pairs is not a routine refactor step.

Before moving a public name in `app/`, search the whole repository for callers,
including top-level `scripts/`, not just `backend/`. Pyright's configured scope
is `app`, so a green backend type check cannot clear those outside callers.

## Generated routes and database predicates

Resolve a conflict in `scripts/route_index.json` by regenerating from the merged
routes: from `backend/`, run `uv run python -m scripts.route_index --emit > scripts/route_index.json`, then
`uv run python -m scripts.route_index --sync scripts/route_index.json`. Run
`uv run python -m scripts.route_index --check` too to validate the frozen
shadowing pairs. If route ordering changed, verify
the identities behind `scripts/route_index_frozen.py`'s indexed pairs and update
their indices and tests together; never freeze a newly introduced shadowing
relationship just to clear the check (`tests/unit/test_route_index*.py`).

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

- `app/**/*.py` over **1500 lines** may not grow — 4 files are already there and
  are frozen at their current size. `.claude/scripts/check-file-sizes.py` judges
  only the files this branch changed, each against the size it had at the merge
  base with `origin/main`.
- From the repository root, run `task ci:fast` before you push: it runs the
  checks your changed paths select, inside a time budget. Add `--types` when you
  touch shared types; `task be:check` runs the whole backend check locally.
- Migrations: [`../.claude/rules/migrations.md`](../.claude/rules/migrations.md)
  — the alembic chain has exactly one head at all times.
- Test pitfalls by symptom:
  [`../.claude/rules/backend-tests.md`](../.claude/rules/backend-tests.md).
- This worktree may already have a uvicorn on port 8081; `task dev` starts
  another. Do not restart a server you did not start.
- A new page under `app/domain/feature_stats/`: register it once, read only
  tables the feature already writes, and keep the questioner away from the raw
  question — [`../docs/manual/dev/feature-stats.md`](../docs/manual/dev/feature-stats.md).
