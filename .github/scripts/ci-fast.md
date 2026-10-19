# ci:fast — bounded local evidence before push

`ci:fast` runs a scoped, time- and memory-bounded subset of CI on the local
machine, so obvious problems (lint, pure-test regressions in
the module you touched, a missing import, a type error in a changed file)
surface before a PR. It is **not** a substitute for full CI: green here means
the selected local checks really ran and passed, nothing more.

```
task ci:fast                 # quick tier: CPU-sized budget, static hooks + tests/types
task ci:fast -- --types      # quick + the full pyright / vue-tsc manual hooks
python3 .github/scripts/ci-fast.py --tier quick   # equivalent without `task`
python3 .github/scripts/ci-fast.py --tier static --pre-push   # the push-time report
```

The bare `python3 .github/scripts/ci-fast.py` (no `--tier`) is the **static**
tier, which is *not* what `task ci:fast` runs — the Taskfile passes
`--tier quick`.

## Tiers

| tier | budget | runs |
|------|--------|------|
| `static` | 180–720s, CPU-sized | only the pre-commit static hooks the path map selects (guards always run). The backend whole-tree guards (`TREE_GUARDS`, pytest `-m pure`, no database) run as one `tree-guards` check when the backend is in scope; no other pytest, no types. This is the legacy direct-invocation default. |
| `quick` | 180–720s, CPU-sized | static hooks **plus** the selected backend `pure` tests and frontend narrow-typecheck / related Vitest. This is what `task ci:fast` runs. |
| `extended` | 900s | the **same selection** as quick, only the whole-tier budget is raised to 900s. It does not expand coverage. |

`extended` is the same work with more wall clock. It does not add suites, and a
run that still exhausts 900s is reported **unknown**, never trimmed to green.

Default budgets use the process's available CPUs, the same affinity-aware
count as the Vitest worker planner: quick/static get `ceil(180 * max(1, 4 / CPUs))`
seconds. One CPU gets 720s, two get 360s, three get 240s, and four or more keep
180s. An unavailable CPU count is treated as one. This is a bounded scheduling
heuristic, not a performance guarantee: a CPU quota or contention can make the
machine slower than its affinity mask suggests.

`--budget` takes precedence over `CI_FAST_BUDGET_SECONDS`, which takes precedence
over the tier default. The environment value must be finite and positive and is
floored at 180s; an explicit `--budget` stays exact, including a shorter bound.
`extended` defaults to 900s. Neither override changes the selected checks or the
per-check timeout (`--timeout`, default 900s). The report records `budget_seconds`
and `budget_source`; exhausting either bound still reports unknown, never pass.

## How the plan's checks are scheduled

The plan's checks are separate processes with separate evidence, so two that
work on different trees run at the same time. `--jobs` sets how many; the
default is 2 when the tier may back it (`--memory-mib` at least 6144, where a
16 GiB runner sits) and 1 below that — the work machine's 3072 MiB session cap
keeps it serial. Checks that share a working directory never overlap: they
write the same tree (Vitest's cache, the generated OpenAPI types, the narrow
tsconfig), so they queue one after another.

The numbers behind that bound, from 12 CI runs of the `fast` job: two checks
dominated every run, `frontend-unit` (850–880s, 4.4–5.2 GiB) and `backend-pure`
(520s p50, ~1.1 GiB). Serially the first consumed the whole 1200s budget and
the second was cut off with what was left; together they fit a 16 GiB runner.
Each check still keeps its own sampled RSS ceiling, timeout and evidence check,
so a killed check is reported killed, never folded into a green or into another
check's result.

`--jobs 1` restores the strictly serial tier. The `ran` rows keep the plan's
order either way, so that part of the report does not depend on which check
finished first; `not_run` and `reasons` are still appended as checks finish.

The ceiling is per check, so `jobs` is also what the machine has to back: at
`--jobs 2` a runner may see up to twice the sampled ceiling. The observed peaks
(5.2 GiB and 1.1 GiB) leave room on a 16 GiB runner; that is why the default
stays at 2 and does not grow with the machine. An interrupt (Ctrl-C, or a
cancelled job) stops every running check before the pool is joined — a check
runs in its own session, so the signal would otherwise not reach it and the
tier would wait out its own timeout. A signal that lands while a check is
being forked, before its pid is known, is held until the check is registered
and then raised where the same cleanup can kill it; the alternative leaves a
process running in its own session after ci-fast has exited.

For example, `CI_FAST_BUDGET_SECONDS=600 task ci:fast` gives a quota-constrained
machine ten minutes without changing repository config. Required CI's draft job
already passes `--budget 1200`, so its bound is unchanged. `--memory-mib` remains
independent (default 3072 MiB, the work machine's session cap).

The `.cheese/pre-push` wrapper retains a separate 240s wall-clock limit,
configurable with `CI_FAST_PRE_PUSH_TIMEOUT`. It can terminate a larger automatic
or environment budget before ci-fast writes its report. That interrupted run is
unverified; raise the wrapper limit as well when using a longer push-time budget.
Direct `task ci:fast` runs do not have this wrapper limit.

## The pre-push entry point

`.cheese/pre-push` in the tree is what the platform's task-worktree `pre-push`
hook runs on every push (`cheese worktree` writes that hook; the mechanism is
described in `docs/workflows.md`). It calls this script as
`--tier static --pre-push`.

`--pre-push` is a modifier, not a tier, and it changes three things:

- It adds the checks in `PRE_PUSH_HOOKS`, which the other tiers do not select:
  today `openapi-document` for a backend-scoped push. Those are steps of a
  Required CI job that a first push can be the first to break, and that a work
  machine can still run — no service, no pinned harness, no node toolchain.
  They are added here rather than to `FAST_HOOKS` because that table is what
  `task ci:fast` and Required CI's draft job read, and neither should spend
  time on a check this entry point is the one trying to close a gap for.
  Measured on a work machine 2026-10-15: 23.3s, 497 MiB peak, against the
  180s static tier budget; the whole entry point run took 46.3s.
- The three hooks that spawn the node toolchain (`eslint`, `stylelint`,
  `boundary-check-frontend`) leave the selection and are recorded in `not_run`,
  never silently dropped: a report from this entry point says it could not
  judge lint rather than reading as a pass that covered it. A push runs on
  whatever box the author has, and a check the machine killed is a red its
  reader cannot act on. The config-drift check still reads the whole hook set,
  so a config that lost one of the three is still unknown, not green. The
  preflight follows the same selection: with no node hook left, a checkout that
  never ran `task fe:install` is not blocked by a missing `frontend/node_modules`
  — the guards and the two python ratchets still run on it.
- Every item that did not pass closes with the command that reproduces it
  outside the hook, and so does each of the three that were left out. The
  reader did not choose to run this and is about to push anyway, so the report
  has to be actionable as it stands. A hook is repeated through pre-commit in
  the invocation this run used — `$CI_FAST_PRECOMMIT` when the caller set one,
  otherwise the default with its interpreter pin (below) — so the repeat runs
  on the interpreter the check ran on. A check that is not a hook — the plan's
  `backend-pure` / `frontend-unit` / `frontend-i18n` /
  `frontend-typecheck-changed`, and a directly invoked `commit-title` — is not
  dressed up as one: it names the command that actually ran, or the input it
  was missing, because `pre-commit run <id>` for one of those exits with `No
  hook with id ...`. A plan check leads with the directory it was spawned in
  and the variables it was spawned under, for the same reason: its argv does
  not carry either, and `uv run pytest` from the repository root does not find
  the runner. Which files are at fault comes from the hook's own output,
  printed above as it runs — the report's rows record the check, its verdict,
  its clock and its command, not its log; the reproduce command prints that
  output again.

The entry point also honours `CI_FAST_PRE_PUSH_REPORT`: when the caller sets it
to a path, the ones of those runs that did not pass leave their output there,
for the caller to carry to whoever asked for the push. The platform's own
delivery push sets it (`backend/sandbox/cheese`), because that push is started
by the `post-commit` hook on a commit an agent made: the report's reader is
that agent, and by push time its terminal is gone, so the report has to be
asked for by name instead of being printed at nobody. A run that passes still
leaves nothing there — the passing report is for whoever ran it by hand, and a
delivery push that reported every clean commit would be noise.

The report carries `"pre_push": true`, and the exit code is unchanged — `1` for
a real failure, `2`/`3` for unknown/blocked — which is what the reproduce
commands and any direct run of the entry point read (a direct run cut by the
entry point's own wall clock is `124`, the one status that is about the run
rather than about a check). What does not read it is the push: that caller
asked for the report by name, and the installed hook exits 0 whatever happens,
because a push refused there is a commit the platform has never delivered and
the checkout it lives in does not survive to retry. `--pre-push` stays honest
about what it checked; blocking a merge remains CI's job, and it stays the
hook's job not to lose the work it is reporting on.

`uvx` puts its tool venv's `bin` first on the hooks' `PATH`, so a
`language: system` hook whose entry says `python3` runs under whichever
interpreter that venv was built from. Measured 2026-10-12 on the agent work
image: 3.9.25 there against the 3.13.5 running this script, which made
`migration-fork` — 3.10+ annotation syntax in
`.claude/scripts/check-migration-fork.py` — report fail on every backend-scoped
run, and made the reproduce line it printed repeat that instead of the check.
The default pre-commit command therefore pins `uvx` to the interpreter running
this script (`uvx --python <sys.executable> pre-commit`): the hooks' `python3`
comes from a venv built on that same interpreter, which is the one the entry
point started this run with. `CI_FAST_PRECOMMIT` replaces the command and the
pin with it; both workflows set that variable to `uvx pre-commit`, so this
default is what a caller that sets nothing — the entry point, `task ci:fast`, a
plain `python3 .github/scripts/ci-fast.py` — runs.

## In Required CI (the PR quick tier)

`.github/workflows/required-ci.yml` reuses this tool for pull requests: one
inline job runs

```
gh api "repos/$GITHUB_REPOSITORY/pulls/$PR_NUMBER" > "$RUNNER_TEMP/pull.json"
ci-fast.py --tier quick --budget 1200 --memory-mib 9216 \
  --pull-request "$RUNNER_TEMP/pull.json"
```

and every product suite is skipped. The aggregating job for a draft is named
`CI required (draft quick)`, while a ready PR gets `CI required`. Promoting a
draft (`ready_for_review`) starts a fresh quick run under the ready PR's check
name. A push to main runs the full tier before dev deployment (see
`docs/workflows.md` §5).

### What the PR quick tier does not judge

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
  The push-to-main full run after the merge is.

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
- A path the map does not name is recorded under `unknown_paths` and judged by
  the guards and the static tier alone; it does not turn the product suites on
  (changed in #4393). Widening them made a quick run need the full suite, which
  it cannot fit, so the whole run answered unknown and the guards' own verdict
  was lost with it. Prose is not an unknown path either: a diff of only `.md`
  files (`DOC_GLOBS`) is named nowhere in the map, and `README.md`, `AGENTS.md`
  and a skill's `SKILL.md` would otherwise turn a docs-only draft into a run
  with no product suite that could answer. The doc checks that do read them
  (anchors, links, titles, repo rules) are guards and run either way.
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
  `frontend/src/test/` or `frontend/scripts/` edit, no merge base, or a change
  that seeds no related root while carrying runtime content — a catalog, a
  ratchet baseline, a stylesheet, a deletion with no surviving root, a path
  outside `frontend/` that the scope map still routes here (a site stylesheet,
  a ratchet script). Whether a change carries that content is read from the
  paths the scope map routed into frontend scope, not from the whole diff: a
  change that also touches the backend brings its `.py` files along, and they
  are not frontend content the suite can execute. Nor is a `.github/` edit: no
  workflow or CI script is imported by the frontend graph, so it leaves a
  related run seeded by the surviving roots and, alone, runs no unit test at
  all (`frontend-unit` is recorded as an omission, not as the full suite or an
  unknown). A path the map does not name is not frontend content either; since
  #4393 it does not even turn frontend scope on. The full type check follows
  the same rule: `.github/` is not part of the compiler project, so it no
  longer forces `pnpm run typecheck`. A backend change does not reach the frontend type check here
  either: whether it moved the API document the frontend's types are
  generated from is decided only by Required CI's `api` job, so a route
  change that breaks those types passes this tier and goes red there. A
  config outside the watched list does not widen by
  itself: `frontend/collab/vitest.config.ts` beside a changed source file still
  stays `related`. The full run must be shown to have
  executed both i18n guard specs. Everything else stays narrow: a deletion
  beside a surviving source root that nothing surviving still imports, and a
  catalog, stylesheet or prose edit next
  to a changed source file, still seed `related` from those roots. Only a change
  that seeds nothing *and* leaves the suite nothing to judge — prose (`.md`), a
  declaration file —
  leaves the tier running the guards alone with `frontend-unit` recorded not-run
  and a reason.

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
  bridges `node_modules`, and checks only the surviving changed files — any
  type error in them fails — after the type check builds the backend's
  OpenAPI document and generates the API types from it
  (`frontend/scripts/typecheck.mjs` — neither half is committed); a deleted non-spec source file forces the full
  typecheck instead, since only the whole project sees its dangling importers.
  Non-spec is read from the name's ending, not from the text: a helper module
  called `migrationContract.spec.helper.ts` is imported by thirteen surviving
  specs, and it is the full typecheck that sees them.
  It is not a full consumer-type check.
- **Not covered here**: contract / integration / build / e2e tests, remote
  checks, and full frontend consumer types. The summary lists every skipped
  item. Full Required CI runs after a push to main and gates dev deployment;
  **ci:fast green does not mean full CI passes.**

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
