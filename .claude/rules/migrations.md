---
paths:
  - "backend/alembic/**"
---

# Alembic migrations — single-head discipline

The chain must have exactly ONE head at all times. Parallel PRs and 采纳 pushes
forked it four times on 2026-08-09/10 alone; every fork kills `alembic upgrade
head`, which reddens CI and aborts the dev deploy.

- After creating or merging any migration, run `uv run alembic heads` — it must
  print exactly one. CI enforces this (`migration-heads` job, ungated), but
  catch it locally first.
- New migration: set `down_revision` to the CURRENT head, **and move the
  sentinel**: `echo <your-revision-id> > backend/alembic/HEAD`. That one-line
  file exists so two concurrent migration PRs collide in GIT (same line →
  CONFLICTING, which GitHub re-evaluates continuously as main moves) instead of
  forking alembic silently — checks go stale the moment a sibling merges, git
  conflicts don't. On 2026-08-12 three green-but-stale PRs forked main into
  THREE heads; the sentinel is what makes that unrepresentable. CI
  (`check-migration-fork.py`) and the pre-commit gate both assert
  sentinel == real tip.
- After merging main into your branch, re-check heads — if main gained a
  migration, rechain yours onto the new head and move the sentinel again.
- Two heads found? Prefer rechaining your own migration. Only reach for
  `alembic merge heads` when both sides are already on main — and first check
  whether a merge migration for that parent pair ALREADY exists (two identical
  merge migrations are themselves two heads; this exact collision happened).
- A migration that landed on main is immutable — never edit or delete it;
  deployed DBs have it stamped. Fix forward.

# Zero-downtime migrations

A deploy runs `alembic upgrade head` while the previous backend is **still
serving**, and swaps containers only after it succeeds
(`deploy/deploy-docker.sh`). So every migration must leave a database the
previous release can still read and write, and must not hold a lock that stalls
it. SQLAlchemy selects every mapped column, so a column the old code still maps
breaks every query on that table the moment the migration commits — #2914
dropped `task.video_url` in the same PR that unmapped it (`694b0dbaf5eb`).

`.claude/scripts/check-migration-safety.py` enforces the rules marked ✓ on
every migration a PR **adds** (CI: `migration-heads` job; locally: the
`migration-safety` pre-commit hook). Migrations on main are not re-judged.

1. **One transaction per migration.** `env.py` sets
   `transaction_per_migration=True`: a migration's locks and backfill row locks
   end with it, not with the last migration of the deploy. `lock_timeout` (10s)
   is session-level and covers every migration. A deploy whose Nth migration
   fails leaves the database at N−1 (no longer all-or-nothing) with the old
   backend serving on it; rerunning the upgrade resumes from there. Another
   reason each migration must be safe under the previous release.
2. **Locks.** DDL on a table the live backend uses (`blocks`, `topics`,
   `tasks`, `agent_sessions`, `deliveries`, …) starts with
   `with_lock_retries("t1, t2")` from `migration_helpers` (on `sys.path` via
   `alembic.ini`), naming every table altered and every table a new or dropped
   foreign key points at. ✓ No pasted `DO $$ … lock_not_available` loops.
3. **Adding a column**: nullable, or NOT NULL with a `server_default`. ✓
   A value computed per row: add it nullable → backfill → `CHECK (c IS NOT
   NULL) NOT VALID` → `VALIDATE` → `SET NOT NULL` (no full scan once a
   validated CHECK exists), each step its own migration or PR.
4. **Dropping or renaming a column or table takes two releases.** ✓ First PR:
   the code stops reading and writing it (remove the mapping; a NOT NULL column
   without a default becomes nullable first). Second PR, after the first is
   deployed: `op.drop_column` / `op.drop_table`. A rename is a new name mapped
   onto the old column (`mapped_column("old_name")`), not `ALTER … RENAME`.
5. **Indexes on existing tables are built `CONCURRENTLY`** inside
   `with op.get_context().autocommit_block():`, dropping an invalid leftover
   first (see `c7e2a91f4d3b`). ✓ Tables created in the same PR are exempt.
6. **Constraints on existing tables**: foreign keys and CHECKs are added
   `NOT VALID` (`postgresql_not_valid=True`) and validated in a separate
   statement; UNIQUE is a `CREATE UNIQUE INDEX CONCURRENTLY` then
   `ADD CONSTRAINT … USING INDEX`. ✓ Every new foreign key column has an index
   that starts with it. ✓
7. **Backfills** are plain SQL or batched (a few thousand rows per batch,
   committed between batches in an `autocommit_block`), idempotent (filter on
   "not yet done"), and tested: migrate to the previous revision, seed, migrate
   to this one, assert (`tests/integration/migration_replay.py`). One that
   touches over ~10k rows or ~10 s goes to a periodic job instead.
8. **No `app.*` imports in migrations.** ✓ A migration runs long after it was
   written, against whatever `app` has become. Copy the constant or function in
   and say where it came from. (Six older migrations do; they stay.)
9. **New JSON columns are `postgresql.JSONB`.** ✓ A field queried by path
   becomes a real column.
10. **Name constraints**: `fk_<table>_<column>_<referred table>`,
    `uq_<table>_<column>`, `ck_<table>_<name>`. Indexes keep
    `ix_<table>_<column>`.

A justified exception sits on the statement, with its reason:
`# migration-safety: allow index-not-concurrent — table holds 3 rows`
(or `allow-file <rule> — <reason>` once in the file). No reason, no exception.

The existing debt (anonymous foreign keys, enums without CHECK, foreign keys
without an index, JSON columns, models vs. migrations drift) is listed in
`backend/tests/support/schema_debt_baseline.json`; the ratchets in
`tests/unit/test_schema_debt.py` and `tests/integration/test_schema_drift.py`
fail on a new entry and on a paid-off one still listed.
