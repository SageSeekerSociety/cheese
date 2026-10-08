# backend/ — for agents working in this directory

Read [`../CLAUDE.md`](../CLAUDE.md) first; this file only adds what applies here.

## The import graph is checked, and a wrong import fails CI

`app/` is layered **api -> domain -> core**. Imports that point backwards, a
route reaching into another domain's models, or a cycle between sibling domains
under `app.domain` are failures — including when the import sits inside a
function. 235 pre-existing violations are frozen in `.importlinter` as exact
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

## Caps and conventions

- `app/**/*.py` over **1500 lines** may not grow — 16 files are already there and
  are frozen at their current size. `.claude/scripts/check-file-sizes.py` judges
  only the files this branch changed, each against the size it had at the merge
  base with `origin/main`.
- `uv run ruff check .`, `uv run pyright` and `uv run pytest tests/ -n 4 -q` are
  the rest of `task check`; `task be:check` runs all four locally.
- Migrations: [`../.claude/rules/migrations.md`](../.claude/rules/migrations.md)
  — the alembic chain has exactly one head at all times.
- Test pitfalls by symptom:
  [`../.claude/rules/backend-tests.md`](../.claude/rules/backend-tests.md).
- This worktree may already have a uvicorn on port 8081; `task dev` starts
  another. Do not restart a server you did not start.
- A new page under `app/domain/feature_stats/`: register it once, read only
  tables the feature already writes, and keep the questioner away from the raw
  question — [`../docs/manual/dev/feature-stats.md`](../docs/manual/dev/feature-stats.md).
