# Pool integration probe, 2026-10-17

Two backend integration shards cannot share one cheese-ci box at the pool's
current four pytest workers per slot. In that configuration the box's 7.76 GiB
of RAM and all 4 GiB of swap were exhausted, and the kernel killed one shard's
pytest worker, losing 197 of that shard's 1,193 assigned cases.

At two workers per slot they can: both shards ran all 1,193 assigned cases,
used 1.93 GiB of swap, and finished in 12m51s and 12m47s — inside
`test.yml`'s 15-minute shard timeout with about two minutes to spare. One shard
alone takes 7m23s, with no OOM, and leaves 1.15 GiB of memory available.

Measurement from PR #4128, workflow `.github/workflows/pool-integration-probe.yml`
(dispatch-only in its delivered form), on `cheese-ci-box-1`.

## What ran

`test.yml`'s `test` job for the integration layer, shard 0/5, was reproduced on
the pool as closely as the pool allows:

- `uv sync --frozen --all-extras`, then a fresh empty per-slot database, then
  `uv run alembic upgrade head`.
- The pytest command from `test.yml`, verbatim: `-m integration -k 'not kotlin'
  --ignore=tests/forgejo -n <workers> -p scripts.ci_shard --ci-shard 0/5`,
  `scripts.ci_evidence`, `--reruns 1`, `--dist loadfile
  --no-loadscope-reorder`, JUnit XML.
- The slot environment from `test.yml`: `CHEESE_CI_SLOT`, `DATABASE_URL` on
  `localhost:5442`, `TEST_PG_BASE`, a 16-database Redis block per slot on
  `localhost:6389`, and the same dummy credentials.
- `CHEESEX_REDIS_DB_COUNT=64` was set explicitly. The resident Valkey serves 64
  databases; the tests' default capacity of 16 rejects the slot-1 block during
  collection, before any test runs.

Two prerequisites from `test.yml` were deliberately not recreated, so the probe
does not provision the machines:

- `ensure-apt.sh` (bubblewrap, iproute2, iptables, lsof, ripgrep, tmux, zsh).
  No integration case in this shard failed for a missing one.
- The pinned Claude Code, Codex and pi builds (`npm install` plus pi's release
  archive, and a Node toolchain). Four cases failed at their prerequisite
  check: three `CHEESE_TEST_PI` setup errors and one `CHEESE_TEST_CLAUDE`
  failure. Their runner time is part of the measurement; their count is
  reported rather than hidden.

The one tool the workflow does fetch is `uv`, through the same pinned
`setup-uv` step `test.yml` uses. It lands in the runner's tool cache, not in
the system, and the dependency environment is per-slot under `RUNNER_TEMP`.

## The box

`cheese-ci-runner-1`, one of the two runner slots on box 1:

| | |
| --- | --- |
| CPU | 8 vCPU, Hygon C86-3G 7390 32-core |
| Memory | 7.76 GiB usable (8,137,536 KB) |
| Swap | 4 GiB (`/swapfile`) |
| Disk | 40 GB, 13 GB free |
| `/tmp` | 3.9 GiB tmpfs (RAM, not disk) |
| Resident Postgres | `cheese-ci-postgres`, 5442, 3 GB tmpfs data directory; 1.45 GiB resident after the single run |
| Resident Valkey | `cheese-ci-valkey`, 6389, 64 databases; 35–63 MiB |
| Other resident work | one `cheese-private-*` container (0.8 MiB at the end) |

The box had 1.45 GiB in use and 0.96 GiB of swap in use before the single run.

## Results

| Configuration | Wall time (pytest) | Peak per-slot memory | Box memory low-water mark | Swap free at the low point | OOM kills |
| --- | ---: | ---: | ---: | ---: | ---: |
| One slot, 4 workers | 7m23s (443.6 s) | 3.66 GiB | 1.15 GiB available | 3.12 GiB | 0 |
| Both slots, 4 workers each | 10m20s / 10m03s | 2.93 / 2.40 GiB | 332 MiB available | 16–32 KB | 1 |
| Both slots, 2 workers each | 12m51s / 12m47s | 2.24 / 2.43 GiB | 574 MiB available | 2.07 GiB | 0 |

"Peak per-slot memory" is the runner service's own cgroup, sampled every
second by `scripts.observe_ci_resources` (page cache excluded). It excludes the
resident Postgres, which is in a container of its own, and the other slot.
"Box memory low-water mark" is `MemAvailable` from a two-second sample of
`/proc/meminfo`; that number sees both slots and the resident pair.

### One slot, four workers

- `uv sync` 4.7 s (warm uv cache; 51.6 MB peak RSS), migrations 13.5 s
  (144.2 MB), pytest 443.6 s (the timed wrapper reports 449.1 s, which
  includes starting the observer).
- 1,193 cases assigned; 1,189 passed, 1 failed, 3 errors, 5 reruns. The four
  failures are the missing pinned builds above; one timing case in
  `tests/integration/test_ask_after_an_unheard_delivery.py` failed once and
  passed on retry.
- Per-slot cgroup peak 3.66 GiB (median 2.96 GiB); largest single process tree
  934 MiB.
- Box: `MemAvailable` bottomed at 1,207,372 KB (1.15 GiB) at 15:48:49Z, when
  6.61 GiB of the 7.76 GiB was in use including reclaimable cache. Swap use
  did not grow beyond the 0.88–0.96 GiB already in use when the job started.
- `oom_kill` in `/proc/vmstat` stayed at 18. The OOM lines in `dmesg` are from
  2026-09-17, the incident `docs/infrastructure.md` records.
- Runnable queue peaked at 22 on 8 CPUs (tests spawn subprocesses), CPU steal
  at most 1%.

### Both slots, four workers each

Both jobs were pinned to the same box and started within the same second, so
they occupied slots 0 and 1 together.

- Pytest wall time 620.3 s and 603.2 s — 40% and 36% longer than the single
  slot. Four tests on slot 0 and two on slot 1 went over the integration
  layer's 30 s ceiling (42.8–53.7 s each) and were rerun.
- `MemAvailable` bottomed at 339,668 KB (332 MiB) at 16:00:18Z. Swap free fell
  to 16–32 KB: all 4 GiB of swap was in use by 16:00:14Z.
- At 16:00:57–58Z the kernel invoked the OOM killer and killed `python3`
  (pid 3229335, 592,568 KB anonymous RSS) inside
  `cheese-ci-runner-1b.service` — slot 1's pytest worker `gw3`. Pytest logged
  `node down: Not properly terminated` and
  `worker 'gw3' crashed while running
  tests/integration/test_account_connection_migration.py::test_own_agents_keep_their_connections_and_go_back_unchanged`.
- Slot 1 therefore executed 996 of its 1,193 assigned cases (991 passed, 2
  failed, 3 errors); 197 cases never ran. Slot 0 executed all 1,193 (1,189
  passed, 1 failed, 3 errors).
- The OOM counter moved 18 → 19 in both logs' samples.
- Per-slot cgroup peaks 2.93 GiB and 2.40 GiB; runnable queue peaked at 116 on
  8 CPUs; IO wait peaked at 93%; CPU steal at 2%.

The kill is the failure mode `docs/infrastructure.md` describes: the kernel
picks a process by score, not by which job caused the pressure, and here it
took a worker from the neighbouring slot. In Required CI the lost cases would
also fail the shard's completeness check — a run that silently misses 197 cases
is not a green shard.

### Both slots, two workers each

This is the mitigation the four-worker result calls for: half the pytest
workers per slot, everything else unchanged. Both jobs again occupied slots 0
and 1 of box 1.

- Pytest wall time 771.0 s and 767.9 s (12m51s and 12m47s) — 74% and 73% longer
  than one slot at four workers, and 24–27% longer than the four-worker pair.
- Both slots executed all 1,193 assigned cases: 1,189 passed, 1 failed, 3
  errors, 4 reruns each. No worker was lost, no `node down`, and no test went
  over the 30 s layer ceiling. The four failures are the missing pinned builds
  listed above.
- Per-slot cgroup peaks 2.24 GiB and 2.43 GiB; largest single process tree
  1.14 GiB.
- `MemAvailable` bottomed at 587,616 KB (574 MiB) at 16:19:23Z. Swap use peaked
  at 1.93 GiB of the 4 GiB file; `oom_kill` stayed at 19.
- Runnable queue peaked at 39–43 on 8 CPUs; IO wait at most 32%; CPU steal at
  most 1%.

## Conclusion

**Conditionally yes**: two integration shards can share one cheese-ci box at two
pytest workers per slot; **no** at the pool's current four.

At four workers per slot the box runs out of RAM, spends its entire swap file,
and the OOM killer terminates a worker, losing cases. At two workers per slot
both shards ran their complete selection (1,193 assigned cases each), swap use
peaked at 1.93 GiB of 4 GiB, and neither worker was lost.

What the two-slot configuration needs:

1. **Two pytest workers per slot**, not four — `CHEESE_CI_TEST_WORKERS=2` (or
   `-n 2`). This is the configuration measured; four workers per slot
   reproduces the OOM in `docs/infrastructure.md`.
2. **One shard per slot, at most two shards per box.** Each slot's cgroup peak
   was 2.24–2.43 GiB, and the resident Postgres held 1.46 GiB. The box's
   `MemAvailable` low-water mark was 574 MiB and 1.93 GiB of swap was in use.
3. **Five shards still fit in one round.** The pool has three boxes, six slots,
   so the current five integration shards can run concurrently with one slot
   spare, each finishing in about 13 minutes.
4. **The 15-minute shard timeout is the binding margin.** 12m51s leaves about
   two minutes, and the pool's boxes share a SAS disk whose latency has bound
   this layer before (`docs/infrastructure.md`, 2026-09-02). A heavier shard or
   a busy disk can cross it; that is the reason to treat this as a conditional
   configuration rather than a drop-in replacement.
5. **`/tmp` is RAM on the pool** (3.9 GiB tmpfs), not disk as on a hosted
   runner; pytest's temporary trees count against the same 7.76 GiB.
6. **The resident Postgres is shared by the box's slots**: 3 GB of tmpfs for
   data, a 300-connection budget, and two runs migrating and dropping databases
   on one server (per-slot names keep them apart).

One shard per box at four workers also works, but then the second slot is idle
and the pool offers no concurrency over the hosted runners.

Not measured here: the pool's wall time against the GitHub-hosted arm64 shards,
and the shared disk under two simultaneous database workloads. Both belong to
the decision to switch Required CI, not to this probe.

## Reproduction

The workflow is dispatch-only. Until this file is on `main`, GitHub does not
offer `workflow_dispatch` for it; the probe runs above were started from
temporary branches (`probe/integration-single`, `probe/integration-pair`,
`probe/integration-pair-2w`) that the final commit removes. After merge:

```sh
gh workflow run pool-integration-probe.yml --ref main -f pair=false
gh workflow run pool-integration-probe.yml --ref main -f pair=true
gh workflow run pool-integration-probe.yml --ref main -f pair=true -f workers=2
```

Each run uploads `before.txt`, the timing files, `box-memory.log`, the JUnit
report, the pytest log, the shard selection and `resources/` as an artifact.

## Limitations

- One box, one run per configuration. The pool has three boxes on one shared
  Proxmox disk; a second box could differ, and the disk is not measured here.
- The runs were sequential on the same box. The two-worker pair started with
  1.06 GiB of swap already in use from the four-worker pair before it; its
  peak (1.93 GiB) is still below the 4 GiB file, but a fresh box could show a
  slightly different low-water mark.
- The `uv` cache was warm (sync took 0.4–4.7 s). A cold cache adds wall time,
  not memory.
- No `ensure-apt.sh` and no pinned harness binaries, as above. The four
  prerequisite failures and their reruns are included in the wall time.
- The probe reports; it does not judge the suite. This file records the
  failures observed and the cases that did not run, but it does not run
  `assert_suite_ran`.
- The first summary script in the probe had an `oom_kill` key typo, so its own
  summary line printed `oom_kill_delta=0`; the kill above is read from
  `dmesg` and from the raw `box-memory.log` samples (18 → 19). The workflow's
  helper is corrected in the delivered version.
- In the probe runs, `before.txt` printed the runner's `.env` worker count
  (`workers=4`); the two-worker runs' effective count is visible in the pytest
  log as `created: 2/2 workers`. The delivered workflow scopes the slot before
  describing the box, so it prints the effective value.
