# .github/ — CI diagnosis

Read [`../AGENTS.md`](../AGENTS.md) first; title rules are already enforced by
`scripts/check-commit-title.py` and documented in `../docs/workflows.md`.

For a completed, failed Required CI PR run, use the repository's `rerun-failed`
dispatch to retry failed jobs instead of pushing an empty commit. A new event
can change scope as main advances, while a rerun preserves the original run's
selection and successful jobs; `workflows/rerun-failed.yml` validates the run
before using its workflow token's Actions permission.

```bash
gh api --method POST repos/SageSeekerSociety/cheese/dispatches \
  -f event_type=rerun-failed -F 'client_payload[run_id]=<run-id>'
```

Inspect `PR title recheck` when a correct title still has an old failure.
`workflows/pr-title.yml` responds to title edits and readiness by retrying
`CI required` for the head's finished run; a cancelled recheck is not a verdict
on the new title. Match the head and run attempt before adding another commit
or restarting unrelated suites.

Tests which invoke real pre-commit need a temporary `PRE_COMMIT_HOME` per test,
as `scripts/test_ci_fast.py` does. Self-hosted jobs share a home, and otherwise
their pre-commit SQLite/cache writes can lock each other.

Metric artifacts and snapshot-page merges follow
[`../docs/manual/dev/AGENTS.md`](../docs/manual/dev/AGENTS.md).
