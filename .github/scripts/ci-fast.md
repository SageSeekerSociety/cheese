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

The quick tier's 180s is the work machine's own limit, not a property of the
selection. A narrowed frontend run seeds the read-off-disk specs beside the
changed roots, and those cost ~70s on the work machine (127 tests, 71.4s wall,
one worker per file, measured when there were fifteen of them): on a tight local
budget the tier can exhaust it and report unknown where the runner (1200s) has
room. That is the budget talking, not the change.

Both bounds are overridable, because they describe the machine rather than the
tier: `--budget` and `--memory-mib` default to the work machine's own limits
(the 3072 MiB default is literally its session cap), and a machine that can back
more gets more. Required CI's draft job passes its own pair (below); `extended`
raises only the budget.

## In Required CI (the draft tier)

`.github/workflows/required-ci.yml` reuses this tool for **draft** pull
requests: one inline job runs

```
gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" > "$RUNNER_TEMP/pull.json"
ci-fast.py --tier quick --budget 1200 --memory-mib 9216 \
  --pull-request "$RUNNER_TEMP/pull.json"
```

and every product suite is skipped. The aggregating job for a draft is named
`CI required (draft quick)` — never the `CI required` branch protection requires
— so a draft's quick green can never satisfy the real gate, even if a selected
suite is skipped. Promoting the draft (`ready_for_review`) starts a fresh
**full** run; the draft-quick green is not reused. `merge_group` entries are
always the full tier. This tier says a draft is buildable; it is not, and cannot
be, the merge gate (see `docs/workflows.md` §6).

### What the draft tier does not judge

A draft is a work in progress, and the tier is red only for what its author can
act on. Three things it does not own, and therefore does not turn red (each is
in the run's `not_run`):

- **A draft's title.** It is the platform's placeholder for the task ("WIP:
  <task title>"), it says nothing about the change, and the author did not write
  it. Both reads of the PR carry the draft flag — the `--pull-request` payload
  the draft job passes, and the `gh pr view` lookup the local entry point uses —
  so ci-fast does not check it either way; `check-commit-title.py` skips a
  draft for the same reason. The `required`
  job checks the title, and `pr-title.yml` re-runs that job when the PR is
  marked ready.
- **Tests that need the pinned Claude Code / Codex / pi builds.** These come
  from the environment (`CHEESE_TEST_*`), installed by full CI and by
  `dev-db.sh`; a test that reaches for one FAILS where they are absent, it does
  not skip (`tests/pinned_claude.py`). Installing three binaries into every
  draft run is the full CI's job, so those tests are named not run instead —
  and left in the run's required files, where the audit names them
  (`not_run_pure`) and reports a change whose every pinpointed consumer was
  dropped that way as `unknown`, not green. A change that still has required
  files the run did execute is judged by them: those are what it was for.
  The files are the transitive importers of the helper and of the variables
  themselves (measured: 30 of 186 for a pi change). This counts import
  statements only: the dynamic-import widening would make `app.main` a harness
  consumer — it loads its routes that way — and drag most of the suite in.
- **A run the tier could not finish.** As everywhere else in this tool, an
  exhausted budget or a check with no input is `unknown` (exit 2), never a red
  it did not observe. What is different here is only how the workflow reads
  that exit: `1` (a real failure) reddens the draft, and so does an exit that
  left no report behind — a run that never reached a verdict, which this step
  must not dress as a pass. `2`/`3` redden it only where ci-fast's own report
  says `unknown`/`blocked`; there they are written into the step summary and a
  warning annotation instead. GitHub has
  no third conclusion, so the summary is where "not judged" is readable; the
  green of `CI required (draft quick)` is not a statement that everything ran.
  The merge queue's full run on the same commit is.

The three together were the bulk of the red: over the two days to 2026-10-12,
of 188 merged PRs 83 failed their first Required CI run; the draft tier went
live at 03:47 on 10-12, and 17 of the 39 first-push failures it created after
that were this tier's false reds — a placeholder title it was never supposed to
own, or tests it was never given the builds for.

This job DOES have Redis (a valkey service, same image and port as every
full-tier job), because a pure test that touches it was red on a connection to
`localhost:6379` — a service this job had simply not been given.

The job keeps the quick tier's *selection* — a draft is never a trimmed full CI
— but not the work machine's *bounds*: it runs on a 4 vCPU / 16 GiB runner, which
gets the ceiling it can back and a budget sized from the full-CI step times.
Handing a draft the 180s / 3072 MiB defaults is what had made this tier red for
every frontend change: of the 12 completed draft runs in the 10-12 03:56–06:30
window, all 12 died in `frontend-unit` (timeout 8, memory-ceiling stop 3,
unmeasurable 1; peaks 2771–3079 MiB against that 3072 cap) with every later
check recorded at 0s. An exhausted budget is still reported **unknown**, never
trimmed to green.

The first run with the runner's bounds (2026-10-12, the widest plan: unknown
paths widen the scope, so it fell back to the full suite and the full type
check) is also what calibrated them: `frontend-unit` — the whole suite, three
forks — **passed in 584s at a 3580 MiB peak**, `frontend-typecheck-full` passed
in 123s, and `backend-pure` then ran out of the 1200s budget. So the ceiling is
wide of what the pool actually uses, and 1200s covers the targeted plan
(change-related tests, changed-file types) with room; a draft that falls back to
everything is still allowed to answer unknown.

## What it proves, and what it does not

- Scope comes from the same `select()` + path map as Required CI; the guards
  run on every invocation. Changes include committed, staged, unstaged,
  untracked, deleted and both sides of a rename.
- A path the map does not name could be any kind of code, so this tier widens
  the scope to both product suites. Prose cannot be, so a diff of only `.md`
  files (`DOC_GLOBS`) is not an unknown path: `README.md`, `AGENTS.md` and a
  skill's `SKILL.md` are named nowhere in the map, and treating them as unknown
  turned a docs-only draft into the full suite, which the tier cannot fit — it
  answered unknown. The doc checks that do read them (anchors, links, titles,
  repo rules) are guards and run either way; a non-doc path next to a `.md`
  still widens.
- Backend test selection is a pure static reverse-import graph over the AST
  (never file-name tokens), plus the always-on tree/i18n guard files. The
  pytest run loads a small audit plugin (`-p ci_fast_pytest_plugin`) that
  records, in the same run, which nodes survived `-m`/`-k` (with their
  fixture-derived layer), which were deselected, and which executed. A green
  run must show every selected `pure` node executed and none hidden by a
  filter; a required consumer file's tests are matched by classname module
  prefix (pytest writes no `file` attribute). Running only the guards never
  proves a changed module has coverage; a change whose modules have no `pure`
  consumer at all is unknown. Excluded `integration`/`contract` nodes are the
  tier working as designed — recorded not-run and owned by full CI — not a
  silent gap. Unresolvable dynamic imports (`import_module(name)`,
  `__import__(expr)`) widen conservatively; a giant strongly-connected group may
  select many tests, in which case the run is honest about the size and may end
  unknown.
- A frontend catalog edit under `frontend/src/i18n/messages/**` is read back by
  the backend refusal/sentences tree guards, but the shared CI path map names
  only `roomNotice`/`apiError`/`global`. This tier widens backend scope locally
  for any such edit so those guards still run here; the shared map — and what
  Required CI runs — is left untouched.
- Frontend Vitest runs the whole suite only when the change cannot be narrowed:
  an edit to one of the six frontend config files this script watches
  (`frontend/package.json`, `frontend/pnpm-lock.yaml`, `frontend/vite.config.ts`
  and the three `frontend/tsconfig*.json`) or to `.pre-commit-config.yaml`, a
  `.github/` edit, a `frontend/src/test/` or `frontend/scripts/` edit, an
  unknown path, no merge base, or a change that seeds no related root while
  carrying runtime content — a catalog, a ratchet baseline, a stylesheet, a
  deletion with no surviving root, a path outside `frontend/` that the scope map
  still routes here (a site stylesheet, a ratchet script). Whether a change
  carries that content is read from the paths the scope map routed into frontend
  scope, not from the whole diff: a backend route change rides in through a
  regenerated `backend/openapi.json`, and its own `.py` files are not frontend
  content — the one reader of that document writes types-only, so the suite has
  nothing to execute for it. A config outside the watched list does not widen by
  itself: `frontend/collab/vitest.config.ts` beside a changed source file still
  stays `related`. The full run must be shown to have
  executed both i18n guard specs. Everything else stays narrow: a deletion
  beside a surviving source root that nothing surviving still imports, and a
  catalog, stylesheet or prose edit next
  to a changed source file, still seed `related` from those roots. Only a change
  that seeds nothing *and* leaves the suite nothing to judge — prose (`.md`), a
  declaration file, or a types-only input such as `backend/openapi.json` — leaves
  the tier running the guards alone with `frontend-unit` recorded not-run and a
  reason.

  A narrowed run is seeded with the changed source roots **and** every spec that
  judges a frontend file by reading it off disk rather than importing it
  (`OFF_DISK_SPECS` in `ci_fast_checks.py`: the specs that assert a
  component's markup, a stylesheet's rules, a page's scroll container or a whole
  directory). A read is not an import edge, so `related` would never select one
  of them for the file it answers for, whatever else the change contains:
  `src/styles/docBlocks.css` is imported by `main.ts` and the doc blocks but not
  by `docBlocks.spec.ts`, which reads it with `readFileSync`; measured, changing
  the `padding-left` of `.doc-status[data-status]` from 6px to 8px leaves both
  i18n guards green and turns only `docBlocks.spec.ts` red. The same read hides
  behind a surviving root when that stylesheet is edited beside a changed
  component, or when the file a spec reads is itself the changed component. A
  deletion is a read only where a spec reads it: a spec that *imports* the
  deleted file is reached by neither the seeds nor this list, and the typecheck
  cannot stand in for it, so that case widens the run instead. The report must
  show each of those
  specs ran, so a selection that silently drops one is unknown, not green, and
  so is a report holding nothing but them: the seeds run whatever changed, so
  passing only them is an empty selection wearing their exit code, which main
  never read as green. A new
  spec that reads the tree this way belongs in that tuple — `test_ci_fast.py`
  holds it against the tree, so a renamed or newly reading spec fails the
  self-test rather than quietly leaving the tuple. The
  narrow type project
  extends the real `frontend/tsconfig.json`,
  bridges `node_modules`, and checks only the surviving changed files against the
  existing tsc-ratchet baseline; a deleted non-spec source file forces the full
  typecheck instead, since only the whole project sees its dangling importers.
  Non-spec is read from the name's ending, not from the text: a helper module
  called `migrationContract.spec.helper.ts` is imported by thirteen surviving
  specs, and it is the full typecheck that sees them.
  It is not a full consumer-type check.
- **Not covered here**: contract / integration / build / e2e tests, remote
  checks, and full frontend consumer types. The summary lists every skipped
  item. Full Required CI + merge queue remains the only merge gate — **ci:fast
  green does not mean full CI passes.**

## Exit codes

| code | meaning |
|------|---------|
| 0 | every selected check actually ran and passed. Not full CI. |
| 1 | a selected check really failed. |
| 2 | **unknown** — nothing could be honestly judged: budget/time exhausted, a check killed by signal, a memory-ceiling stop, an empty or skipped test report, a selected file that produced no executed case, an unavailable merge base, a hook/config drift, a worktree that changed during the run, a `--pull-request` payload that could not be read (the title is the only check that costs), or an unreadable report. |
| 3 | **blocked** — a required tool or dependency is missing (`backend/.venv`, `frontend/node_modules`, pre-commit, a machine without `ps`), or the platform is not Linux. |

A single missed memory sample is not blocked. The sampler rides a few misses
out, then stops measuring that check and leaves its verdict to the exit code
and the clock; only `ps` itself being absent is blocked, because then no ceiling
can be enforced at all. (Tying the sample's window to the check's remaining
budget used to make a miss out of a nearly spent check, and that read as a
missing tool — it turned an expected unknown into blocked twice on #3476,
2026-10-12.)

Unknown (2) and blocked (3) are never green: when the tier runs out of budget
the remaining checks are recorded as not-run with a reason, and the run stops
short of a pass. A report is written to `ci-fast-report.json` in the git dir;
green is only as good as that report being on disk and matching the current
head, base and dirty-tree fingerprint.

## Bounds

- Per check and for the whole tier, wall-clock is bounded; over budget is
  unknown.
- Memory is sampled process-group RSS (Linux `ps`) on the group the runner
  creates, capped at `--memory-mib` (default 3072 MiB — the work machine's
  session cap; up to 9216 MiB, refused above). This is not address-space
  enforcement: a very short peak can be missed, and a child that opens its own
  session is outside the group. Whatever cap the machine itself imposes stays
  the hard limit. A check whose sampling could not be read at all is recorded
  `unmeasured: true` in the report rather than given a ceiling of 0; the
  wall-clock bound still applies to it, though on a machine where `ps` answers
  slowly the kill follows the deadline by up to one sampling window (about a
  second). No verdict turns on that.
- The frontend's Vitest parallelism is derived from that same cap
  (`vitest_workers`): one fork per 3072 MiB, also bounded by cores − 1 and 16 —
  the rule `frontend/vite.config.ts` already applies to the same box (a fork
  peaks at 1.7–2.1 GB). A local quick run therefore still gets one worker, and
  keeps the in-process, one-file-at-a-time form it had before; a runner whose
  ceiling can back several gets several — measured at 3580 MiB for the whole
  suite on three forks, parent process included, against 9216 MiB.
- The environment handed to each check is scrubbed of variables that would
  change its verdict (`SKIP`, `PRE_COMMIT_COLOR`, stray `GIT_*`, `PYTEST_ADDOPTS`,
  `NODE_OPTIONS`); `PATH`/`HOME` are kept so tools resolve.
