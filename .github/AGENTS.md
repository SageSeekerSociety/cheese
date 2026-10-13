# .github/ — CI diagnosis

Read [`../AGENTS.md`](../AGENTS.md) first; title rules are already enforced by
`scripts/check-commit-title.py` and documented in `../docs/workflows.md`.

For a completed, failed Required CI PR run, use the repository's `rerun-failed`
dispatch to retry failed jobs instead of pushing an empty commit. A new event
can change scope as main advances, while a rerun preserves the original run's
selection and successful jobs; `workflows/rerun-failed.yml` validates the run
before using its workflow token's Actions permission. The exact
`POST /repos/{owner}/{repo}/dispatches` body is in that workflow's header.

Inspect `PR title recheck` when a correct title still has an old failure.
`workflows/pr-title.yml` responds to title edits and readiness by retrying
`CI required` for the head's finished run; a cancelled recheck is not a verdict
on the new title. Match the head and run attempt before adding another commit
or restarting unrelated suites.

`CI required` also fails its `review-pin` step when no independent review is
pinned at the PR's head or the newest one lists a blocker or major
(`scripts/check-review-pin.py`). Pin a review with `.claude/scripts/pr-review.sh`
after the last push; `workflows/review-pin.yml` re-runs `CI required` for the
head's finished run when the pin arrives, so no empty commit is needed.

Tests which invoke real pre-commit need a temporary `PRE_COMMIT_HOME` per test,
as `scripts/test_ci_fast.py` does. Self-hosted jobs share a home, and otherwise
their pre-commit SQLite/cache writes can lock each other.

Metric artifacts and snapshot-page merges follow
[`metric-snapshots.md`](../.claude/rules/metric-snapshots.md).
