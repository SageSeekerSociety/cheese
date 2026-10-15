# First-push CI failures, 7 days to 2026-10-15

Of 592 PRs merged into `main` in the 7 days to 2026-10-15T06:00Z, **124 (20.9%) left
`CI required` red on their first head**, and 183 (30.9%) red at least one leaf job there.
Nearly all of those reds sit in jobs a work machine cannot reproduce: the service-backed
backend shards, e2e, and the frontend node toolchain.

One step was both uncovered and reachable: the `backend / static` job's
`uv run python -m scripts.export_openapi --check`. It is now selected by the pre-push entry
point, at **23.3s and 497 MiB peak** against the static tier's 180s budget. It speaks to 8
of the 592 first heads. The broader finding is that the entry point's ceiling is low: 13 of
the 124 gate reds (10.5%) came only from jobs it can run, its existing selection already
covered 9 of those 13, and 2 more were a runner-side `npm ci` that no local check stands in
for.

## Population and method

The unit is one merged PR. Its **first head** is the head SHA of the earliest
`required-ci.yml` `pull_request` run on the PR's branch within the PR's lifetime. Every job
of every run on that head is read; a job name that concluded `failure`, `timed_out` or
`action_required` is recorded once per PR. Three names only restate another job's verdict and
are dropped: `CI required`, `CI required (draft quick)`, `scope`. Two more are kept but are
not leaf judgements either — `fast`, the draft tier's inline verdict, and
`backend / Backend coverage complete`, the backend matrix's aggregator — so the ranking below
marks those two instead of calling the whole column leaves.

The conclusion of `CI required` — the one branch protection reads — is kept per PR, and a PR
whose first head ever left it red is a **gate red**. A head counts even when it was
superseded: 4 of the 124 had a run cancelled while another run on the same head had already
concluded `failure`. What does not count is a head whose runs all ended before the gate
concluded.

Two windows, both ending at the 2026-10-15T06:00Z cutoff and observed at 2026-10-15T07:19Z:

| Window | Merged PRs | Any red job | Gate red |
| --- | ---: | ---: | ---: |
| 2026-10-08T06:00Z → 2026-10-15T06:00Z | 592 | 183 (30.9%) | 124 (20.9%) |
| 2026-10-13T13:00Z → 2026-10-15T06:00Z | 155 | 40 (25.8%) | 20 (12.9%) |

The second window starts just after the pre-push entry point landed (`1fa3dbcc`,
2026-10-13 12:12:40Z, #3592), so it is what the first-push population looks like with the
entry point in place.

Every PR in the first window had an observed first head. A first head whose run was
superseded before `CI required` concluded is not a gate red even when a leaf job is red;
17 of the 31 PRs whose first head red `backend / static` are in that position, so the
gate-red column understates first-push work and the any-red column is the one to use for CI
rounds.

The per-PR rows behind every number here are in
[`first-push-jobs-2026-10-15.csv`](first-push-jobs-2026-10-15.csv) for the 7-day window and
[`first-push-jobs-2026-10-15-post-pre-push.csv`](first-push-jobs-2026-10-15-post-pre-push.csv)
for the second, one row per merged PR: the window, the PR number, its branch, `merged_at`,
the first head, whether that head was superseded, the gate's conclusions on it, and the red
jobs. Both come from `python3 .claude/scripts/first_push_jobs.py --since … --until … --cache …
--output … --csv …` (added in `6a5cdd23`), which caches every GitHub response — the merged-PR
listing included — so a re-run over the same window with the same cache directory is free and
deterministic. The job-log reads in the next section are not reproducible from the repository;
each one is named by the PR it came from.

## Ranked first-head red jobs

Jobs, by the number of PRs in the 7-day window whose first head left each red. The first two
rows are the kept aggregators rather than leaves; among leaf jobs, `backend / static` (31) and
`frontend / check` (29) lead.

| Job | PRs | Reachable on a work machine | Covered before this change |
| --- | ---: | --- | --- |
| `backend / Backend coverage complete` | 55 | no — aggregator of the service-backed shards | — |
| `fast` (draft quick tier, aggregator) | 41 | no — it *is* the CI-side tier | — |
| `backend / static` | 31 | partly | partly |
| `frontend / check` | 29 | no — node toolchain | no, by design |
| `backend / pure (0/1)` | 23 | runs locally, but the tier's pure selection takes ~7 min | no — over the cap |
| `backend / pure (0/2)` | 16 | runs locally, but the tier's pure selection takes ~7 min | no — over the cap |
| `frontend / unit (3/4)` | 15 | no — node toolchain | no, by design |
| `guards / guards` | 13 | yes | yes |
| `backend / integration (1/4)` | 10 | needs services | no |
| `backend / integration (3/4)` | 10 | needs services | no |

The counts cannot be added: a PR appears in every row it red.

`fast` is the draft tier's own verdict, and its count is the least durable of these: the job
first appears on 2026-10-12, 23 of its 41 logs carry `commit-title: fail`, and every one of
those 23 is from 2026-10-12 — before `74608e80` (#3540) made the draft tier stop judging a
draft's placeholder `WIP: …` title. None of the 13 runs from 10-13 and 10-14 has one. It is
not a gap the local entry point can close by adding a check either way.

## What the reachable jobs actually failed on

For the three jobs whose steps a work machine can run, every red job log in the 7-day window
was read (31/31, 13/13 and 29/29 — a complete enumeration of each population, not a sample).
A job that fails more than once in a run contributes each failing step.

| Job | Failing step | Count | Already covered |
| --- | --- | ---: | --- |
| `backend / static` | `pre-commit run --all-files ruff` | 20 | yes (`ruff`) |
| | `scripts.export_openapi --check` | 8 | **no — added by this change** |
| | `pytest --collect-only` | 2 | no — see below |
| | `check_boundaries.py --self-test` | 1 | no — see below |
| | `pre-commit run --all-files boundary-check` | 1 | yes (`boundary-check`) |
| | `git fetch --no-tags --unshallow origin` | 1 | runner setup, not a check |
| `guards / guards` | `pre-commit run --all-files file-size` | 9 | yes (`file-size`) |
| | `npm ci --no-audit --no-fund` | 3 | setup, not a check |
| | `pre-commit run --all-files repo-rules` | 1 | yes (`repo-rules`) |
| `frontend / check` | `pnpm run typecheck` | 12 | no — node toolchain |
| | `pnpm run lint` | 8 | no — node toolchain |
| | `lint:scenes` / `lint:a11y` | 2 / 2 | no — node toolchain |
| | `lint:style`, `lint:scene-debt`, `test:collab`, `check:request-paths`, `lint:i18n` | 1 each | no — node toolchain |

The frontend job is one block of node toolchain this entry point deliberately leaves out —
its hooks get killed on a small box, and a report that calls a killed check red is worse than
no report. The patterns in the other two jobs are the evidence for the change below.

## The change

`PRE_PUSH_HOOKS` in `.github/scripts/ci-fast.py` now adds `openapi-document` for a
backend-scoped push, and the id is declared in `.pre-commit-config.yaml` as a
`stages: [manual]` hook so the entry point's drift check has something to find. It is the
`backend / static` job's `export_openapi --check` step put behind a hook id — the job runs it
directly, with the venv already synced; nothing about CI changed.

- Not in `FAST_HOOKS`: that table is the selection `task ci:fast` and Required CI's draft job
  both read, and an id added there would change what CI runs and spend the draft tier's 1200s
  budget on a check only this entry point needs.
- `stages: [manual]` for the reason the other heavy hooks carry it: too much for a commit
  gate.
- The untyped ratchet beside it (`backend/openapi-untyped.json`) only shrinks, so a new route
  declares what it returns; that is the failure `3285` and `3470` show.

What it is worth: a developer can see a ruff or boundary failure by running the hooks, and
cannot see this one — the document is generated from the app and compared to two files beside
it that nothing local reads. So the check covers a class of first-push red that no other
locally reachable check can.

## Cost

Measured on a work machine (8 cores, 16 GB) against the real repository, 2026-10-15:

| Run | Wall clock | Peak RSS |
| --- | ---: | ---: |
| `openapi-document`, as the entry point reports it | 23.27s | 497.6 MiB |
| Whole `--pre-push` entry point | 46.27s | — |
| Whole `--pre-push` entry point, on the delivery push | 49.93s | — |

The same command measured 45.09s for the check and 109.61s for the whole entry point on the
same box while other work was running, which is the useful end of the range: the numbers
above are an idle machine. Budgets: the static tier's is 180s, the entry point's own wall
clock cap is 240s (`CI_FAST_PRE_PUSH_TIMEOUT`), so the added check uses 13–25% of the tier
budget and the run stays under half the cap. It is the last hook in the selection and the
only unguarded one that costs tens of seconds; if a later tier ever eats the budget before it,
its own per-check timeout reports it unknown rather than as a red — the same outcome the
existing python ratchets already have, and the entry point still exits 0.

## Coverage

Against the 124 gate-red first heads of the 7 days:

| Statement | PRs | Share |
| --- | ---: | ---: |
| Gate red only from jobs the entry point can run | 13 | 10.5% |
| …of those, failing step already covered by its selection | 9 | 7.3% |
| …of those, the step added here | 2 | 1.6% |
| …of those, a dependency install that no local check speaks to | 2 | 1.6% |
| First heads where the added step failed at all | 8 | of 592: 1.4% |
| Gate reds where the added step failed (alongside others or alone) | 4 | 3.2% |

The added check alone would have turned 2 gate reds green (`3285`, `3470` — red `backend / static` and nothing else). Of the 8 first heads where it failed, the other 6 are `3207` (superseded before the gate concluded), `3480` (also `fast`), `3526` and `3656` (also the service-backed shards), `3543` (also `backend / pure` and `fast`), and `3739` (also `frontend / check`). In the post-entry-point window the same accounting gives 1 of 20 gate reds reachable and 0 where the added step was the only difference.

Of the 9 already covered, 4 are `backend / static` ruff and 5 are `guards / guards` file-size. Of the 2 install failures (`3402`, `3456`), both are `guards / guards` failing at `npm ci`, which is a runner-side dependency install, not a check any local selection can stand in for.

So this change moves the metric by single-digit percent at best. That is the measurement, not
a shortfall of the change: the first-push population is dominated by jobs this box cannot
run, and the reachable slice was already almost fully covered. Raising the metric further
needs either runnable services on the work machine (integration, contract, e2e) or a decision
about the node toolchain, neither of which is this task's scope.

## Left out, with reasons

- `backend / static`'s `pytest --collect-only` (2 PRs) — measured at 67.3s and 616 MiB
  (12,919 of 12,922 cases collected). It reads the whole test tree rather than the diff, and
  judging the shard plugin covers every change or none; 37% of the tier budget for 2 PRs in a
  week does not buy its place.
- `check_boundaries.py --self-test` (1 PR) — 31.5s and 133 MiB, cheap enough, but it proves
  the *checker* fires while the `boundary-check` hook it guards is already selected; 1 PR in
  a week does not buy its place in a 240s cap.
- `backend / pure` (42 PRs across its shards) — the only sizeable job with no service
  dependency (`test.yml` gives it `--no-postgres` and a URL that cannot connect), so it is
  the one gap here that is about cost rather than reachability: the tier's own pure selection
  for one changed module took about 7 minutes before the run was stopped at 65% (≈4.5 min on
  a 240s cap), and the full layer is what `task ci:fast` is for, not the push.
- Everything over the services: `backend / integration`, `backend / contract`, `e2e`,
  `equivalence`, `remote`. These need Postgres and the service pool; the pre-push entry point
  has no way to stand them up.
- `frontend / check` and `frontend / unit` — node toolchain, a deliberate exclusion.
- `pyright` — measured: OOMs on this box (exit 250 after 2m51s with the default memory
  group).

## Verification

- `.github/scripts/test_ci_fast.py` — 114 tests, `OK`, via the workflow's own invocation
  (`CI_FAST_PRECOMMIT="uvx pre-commit" python3 -m unittest discover -s .github/scripts -p test_ci_fast.py`).
  Three new tests: the added checks run under `--pre-push`, do not run without it, and follow
  the backend scope. Each was observed failing against its own mutation of the selection
  (drop the `if args.pre_push:` block → the first; drop the condition → the second; drop the
  scope lookup → the third), and each of the other two stayed green in all three runs.
- `backend/tests/unit/test_ci_latency_contract.py` — one new test, because the three above
  judge `ci-fast.py` against a fixture config: a config that lost the new hook, or lost
  `stages: [manual]`, would leave all of them green while every backend push reported
  `unknown` or every commit paid 23s. It reads the real `.pre-commit-config.yaml` and pins
  the stage, the `files:` scope, and that the entry runs the same `export_openapi --check`
  as the `static` job's step. Observed failing on each of those three mutations separately.
- The check was run end to end through the entry point: `openapi-document: pass (23.27s;
  sampled RSS 497.6 MiB)`, whole run `0` in 46.27s.
- Negative control: zeroing `backend/openapi.sha256` took the entry point to exit 1 with the
  digest-mismatch message; the tree was restored afterwards.

Not run here: the CI draft tier and the full `backend / static` job. The unittest suite that
covers this file is the one the workflow runs; the acceptance card's required checks are the
gate.
