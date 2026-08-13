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
