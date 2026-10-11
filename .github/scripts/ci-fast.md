# ci:fast — bounded local evidence before push

`ci:fast` runs a scoped, time- and memory-bounded subset of CI on the local
machine, so obvious problems (lint, oversized files, pure-test regressions in
the module you touched, a missing import, a type error in a changed file)
surface before a PR. It is **not** a substitute for full CI: green here means
the selected local checks really ran and passed, nothing more.

```
task ci:fast                 # quick tier (default): 180s, static hooks + tests/types
task ci:fast -- --types      # quick + the full pyright / vue-tsc manual hooks
python3 .github/scripts/ci-fast.py --tier quick   # equivalent without `task`
```

The bare `python3 .github/scripts/ci-fast.py` (no `--tier`) is the **static**
tier, which is *not* what `task ci:fast` runs — the Taskfile passes
`--tier quick`.

## Tiers

| tier | budget | runs |
|------|--------|------|
| `static` | 180s | only the pre-commit static hooks the path map selects (guards always run). No pytest, no types. This is the legacy direct-invocation default. |
| `quick` | 180s | static hooks **plus** the selected backend `pure` tests and frontend narrow-typecheck / related Vitest. This is what `task ci:fast` runs. |
| `extended` | 900s | the **same selection** as quick, only the whole-tier budget is raised to 900s. It does not expand coverage. |

`extended` is the same work with more wall clock. It does not add suites, and a
run that still exhausts 900s is reported **unknown**, never trimmed to green.

## What it proves, and what it does not

- Scope comes from the same `select()` + path map as Required CI; the guards
  run on every invocation. Changes include committed, staged, unstaged,
  untracked, deleted and both sides of a rename.
- Backend test selection is a pure static reverse-import graph over the AST
  (never file-name tokens), plus the always-on tree/i18n guard files. A green
  run must show the selected consumer files actually executed in the JUnit
  report; running only the guards never proves a changed module has coverage.
  Unresolvable dynamic imports (`import_module(name)`, `__import__(expr)`)
  widen conservatively; a giant strongly-connected group may select many tests,
  in which case the run is honest about the size and may end unknown.
- Frontend Vitest uses `related` only when a changed source root seeds the
  graph. A config edit, a deleted path, an unknown path, or no merge base falls
  back to the full Vitest run, which must be shown to have executed both i18n
  guard specs. The narrow type project extends the real `frontend/tsconfig.json`,
  bridges `node_modules`, and checks only the changed files against the existing
  tsc-ratchet baseline — it is not a full consumer-type check.
- **Not covered here**: contract / integration / build / e2e tests, remote
  checks, and full frontend consumer types. The summary lists every skipped
  item. Full Required CI + merge queue remains the only merge gate — **ci:fast
  green does not mean full CI passes.**

## Exit codes

| code | meaning |
|------|---------|
| 0 | every selected check actually ran and passed. Not full CI. |
| 1 | a selected check really failed. |
| 2 | **unknown** — nothing could be honestly judged: budget/time exhausted, a check killed by signal, a memory-ceiling stop, an empty or skipped test report, a selected file that produced no executed case, an unavailable merge base, a hook/config drift, a worktree that changed during the run, or an unreadable report. |
| 3 | **blocked** — a required tool or dependency is missing (`backend/.venv`, `frontend/node_modules`, pre-commit), or the platform is not Linux. |

Unknown (2) and blocked (3) are never green: when the tier runs out of budget
the remaining checks are recorded as not-run with a reason, and the run stops
short of a pass. A report is written to `ci-fast-report.json` in the git dir;
green is only as good as that report being on disk and matching the current
head, base and dirty-tree fingerprint.

## Bounds

- Per check and for the whole tier, wall-clock is bounded; over budget is
  unknown.
- Memory is sampled process-group RSS (Linux `ps`) on the group the runner
  creates, capped at 3072 MiB. This is not address-space enforcement: a very
  short peak can be missed, and a child that opens its own session is outside
  the group. The work machine's per-process cap stays the hard limit.
- The environment handed to each check is scrubbed of variables that would
  change its verdict (`SKIP`, `PRE_COMMIT_COLOR`, stray `GIT_*`, `PYTEST_ADDOPTS`,
  `NODE_OPTIONS`); `PATH`/`HOME` are kept so tools resolve.
