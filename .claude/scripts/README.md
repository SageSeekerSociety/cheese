# .claude/scripts/ — repo tooling, not the product

Everything here runs on a work machine or in CI *for this repository*. None of
it ships: a hosted repository never receives this directory, and nothing under
`backend/app` may call into it. When a helper here and a product module both
need a behaviour, the product owns the real implementation and the helper
carries its own copy; a product test holds the two copies together, as
`tests/unit/test_trace_sweep_scrub_in_sync.py` does for the transcript sweep's
secret scrubber.

Two kinds of file live here. Guards and helpers that judge *this tree* are
covered by the checks in `.github/workflows/repo-guards.yml`; their tests sit
beside them as `test_*.py` and that workflow collects them by glob. The rest are
tooling for running the repository — the scripts below are of this second kind.

## Ours to run the repo, not platform features

These six scripts implement how we dispatch and patrol work on this repository
during development. They read the platform's API and GitHub on behalf of
whoever is orchestrating, and they are meaningless to any other project — they
name this project's channels, timers and watchdog. They must not be mistaken
for platform capability, and they are not part of any delivered change.

| script | what it does |
|---|---|
| `.claude/scripts/orchestrator_sweep.py` | Reads every open task line in a channel and lists the ones that need a decision (queued, acceptable, waiting on an answer, red with nobody on it). |
| `.claude/scripts/orchestrator_autoaccept.py` | Judges a filed card against our five acceptance conditions and says whether it may be accepted, and by whom. |
| `.claude/scripts/orchestrator_actions.py` | The shared helpers the other five call: environment recovery, recovery wakes. |
| `.claude/scripts/orchestrator_metrics.py` | Counts our own throughput and lead time — merged pulls, who contributed what, per week. |
| `.claude/scripts/orchestrator_remount.py` | Remounts a work machine's scripts after one is recycled. |
| `.claude/scripts/wake_orchestrator.py` | Emits the heartbeat and the periodic nudge that keep the orchestrating session awake. |

They are referenced by the timers and watchdog running outside this repository,
which is why they stay where they are: moving one breaks the loop that calls it
by path. A change to any of them is a change to our process, not to the
product, and needs no product test.
