# CI feedback measurements

Use `.github/scripts/ci-feedback-report.py` to collect runs created within a fixed
window from GitHub Actions. It requires Python 3.11 or later and an authenticated `gh` CLI with read
access to the repository's Actions runs.

```sh
python3 .github/scripts/ci-feedback-report.py \
  --repo SageSeekerSociety/cheese \
  --workflow required-ci.yml \
  --since 2026-09-22T05:45:00Z \
  --until 2026-09-22T18:35:00Z \
  --output-dir logs/ci-feedback-20260922
```

Use a new output directory for a new observation window. Repeat the same command
to resume an interrupted collection. Keep the raw responses with the report so
another reader can check its inputs. Do not commit raw API responses or local logs.
The window includes runs according to their creation time. Outcomes are observed
at collection time, so they may include jobs that completed after the window ended.
If the collector script changes, use a new output directory because each collection
records the script's SHA-256 hash.

## Interpretation

PR runs and merge-group runs describe different stages and are reported
separately. A merge-group result validates the candidate combined with the target
branch. Its elapsed time does not include time spent waiting to enter that group.

The elapsed-time starting point is the API's workflow `created_at`, not the Git
commit time or an observed push event. This is a proxy for feedback time. The end
is the latest job completion time, not workflow `updated_at`, which can change
after completion. Parallel job durations must not be added to calculate elapsed
time.

Cancelled and unfinished runs remain visible in the outcome counts. Rerun
attempts remain attached to their original workflow run. A success after a rerun
therefore includes the intervening wait when measuring from workflow creation.
Do not interpret workflow failures as a flaky-test rate: product regressions,
infrastructure failures, and tests passing only on retry require separate log
evidence.

Queue time is available only when the job response includes both creation and
start timestamps. Missing or invalid timestamps are reported as unknown rather
than zero. Every percentile includes its sample count; p90 uses the nearest-rank
method.

The feedback targets and remaining optimization work are tracked in
[issue #1279](https://github.com/SageSeekerSociety/cheese/issues/1279).

## Recurring measurement

CI feedback measurement runs at 00:23, 06:23, 12:23 and 18:23 UTC. Each run measures one
non-overlapping six-hour UTC block after a further six hours for runs to settle. A manual dispatch may supply both UTC bounds to reproduce a
different fixed cohort. Each run retains the report, immutable attempt records,
raw paginated API responses, inputs and progress log for 90 days.

The report keeps pull-request and merge-group events separate. It reports
first-attempt outcomes and the cohort-tail first-attempt success streak separately
from latest outcomes after reruns. Timing is also grouped by the jobs that
actually ran, so a documentation-only selection is not evidence for a full
backend or E2E selection. The workflow records observations only; it does not
activate a gate.

The scheduled collector includes Required CI and Remote execution acceptance,
keeping pull-request, merge-group, push, and manual events separate. Browser E2E
and both remote-execution jobs publish a small `evidence.json` receipt identifying
the run, attempt, revision, and suite. The collector validates that identity and
requires every instrumented suite's receipt before counting a clean run. A test
retry, resumed test execution, skipped case, or workflow rerun cannot count as clean.
Missing, expired, malformed, or contradictory receipts are unknown. A successful Required CI
selection without browser E2E is not applicable to the browser clean-run streak.

`cohort_tail_consecutive_clean_first_attempts` counts only consecutive clean runs
at the end of this window. Unknown or unsuccessful runs interrupt the streak.
It is distinct from the workflow-success streak, which does not inspect test
retries. Older runs without receipts remain unknown. Evidence availability is
refreshed on resume while the original attempt records remain unchanged; raw
artifact inventories and downloaded receipts are retained beside the report.
To assess the 20-clean-run criterion across multiple scheduled windows, collect
the full contiguous interval in one report. Do not add window-tail streaks or mix
different workflows and events. Reaching 20 is evidence for reviewing the gate,
not an automatic change to branch protection.
