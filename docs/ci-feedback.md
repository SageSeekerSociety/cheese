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
