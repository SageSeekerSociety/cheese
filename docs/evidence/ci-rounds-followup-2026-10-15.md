# Post-rule CI-head extension, 2026-10-15

Extending the same 66-PR cohort from the first cutoff to **2026-10-15 01:00:00 UTC** adds four distinct heads. The cohort now has **147 heads / 66 PRs = 2.227 heads per PR**, up from 143 / 66 = 2.167 at the prior cutoff. It is 0.100 heads per PR (4.28%) below the frozen baseline's 2.327, but it stays above the strict target of fewer than two, and 13 of the 66 PRs remain open (right-censored), so the all-PR mean is not a completed-lifetime mean. This is an updated observation, not evidence that the pre-commit rule caused a change.

## Fixed population and bounds

The fixed population is exactly the 66 PR numbers in the [first follow-up table](ci-rounds-followup-2026-10-14.csv). Its birth interval is **2026-10-14 11:58:55 UTC <= created_at < 2026-10-14 23:34:57 UTC**. This extension observes required-ci.yml `pull_request` runs through **2026-10-15 01:00:00 UTC**, with a snapshot time of **2026-10-15 01:05:51 UTC**.

The count remains one distinct head SHA per PR. Runs are included when `created_at <= run.created_at < min(PR closure, observation cutoff)`. Same-head reruns and promotions count once, merge-group executions do not count, and zero-run PRs remain in the denominator. A full judgement still requires the exact check name `CI required`; `CI required (draft quick)` is not full green.

The collector also saw five PRs born after the fixed birth cutoff (#3782–#3786). They are excluded from this report. The previous snapshot and the frozen baseline were not recollected or replaced.

## Cutoff comparison

| State at cutoff | First cutoff PRs | First cutoff heads | Extension PRs | Extension heads | Extension mean |
| --- | ---: | ---: | ---: | ---: | ---: |
| All | 66 | 143 | 66 | 147 | 2.227 |
| Merged | 50 | 100 | 52 | 105 | 2.019 |
| Open | 15 | 43 | 13 | 42 | 3.231 |
| Closed unmerged | 1 | 0 | 1 | 0 | 0.000 |

Relative to the frozen baseline of 1,462 PRs / 3,402 heads / 2.327, the extension is 0.100 heads per PR lower (4.28%). The merged extension (52 PRs / 105 heads / 2.019) is the completed-lifetime slice of this cohort: those PRs closed by merge, so their head counts are final. The all-PR extension is not a completed-lifetime mean, because 13 PRs remain open and right-censored; their lifetime counts can still grow. Two PRs (#3726 and #3742) changed from open to merged, and the right-censored count fell from 15 to 13.

Four new heads appeared after the prior cutoff, one each on #3730, #3741, #3775 and #3781. No prior head disappeared. Five new workflow runs produced these four heads because #3781 also had a same-head duplicate run, which is counted once.

The distribution is now 0:1, 1:29, 2:15, 3:10, 4:4, 5:3, 6:1 and 7:3. The median remains 2; 36 of 66 PRs have at least two heads. Exact full judgements cover 108 of 147 heads. The extension's attribution snapshot covers 36 multi-head PR timelines; attribution remains observational and incomplete.

## Equal-age sensitivity (reference, not recomputed)

This extension adds no equal-age checkpoint. The [first follow-up](ci-rounds-followup-2026-10-14.md) already showed the direction reversing across horizons: first-H-hours means rose at H = 2 (+4.43%) and H = 4 (+4.62%), then fell at H = 6 (−14.68%) and H = 8 (−17.88%). Because the sign flips between four and six hours, no single horizon describes the change, and a decrease at one horizon would hide the increase at another. This report does not recompute that table.

## Per-PR evidence and reproduction

The [extension per-PR CSV](ci-rounds-followup-2026-10-15.csv) lists all 66 fixed PRs, their state at the extension cutoff, censoring, current and previous head counts, added heads, exposure, exact-full judgements and first run time per head. Its head counts sum to 147; the added-head column sums to four. Every row matches the unchanged standard reporter for head count, state, censoring, exposure and exact-full judgements.

The extension archive holds the all-PR collection, the filtered fixed-cohort snapshot, the standard report, the per-PR table, the comparison output and a checksum manifest. The filtered snapshot retains the original 66 PRs and 168 matched runs; later-born PRs are retained only in the all-PR source for auditability.

```sh
python3 .claude/scripts/ci_rounds.py ci-rounds-extension-fixed-20261015-010000.json \
  --output reproduced-extension-report.json
python3 -m unittest discover -s .claude/scripts -p 'test_ci_rounds.py'
```

Historical check attempts are incomplete. The saved run list supports the distinct-head count; attribution and promotion evidence are lower bounds. No required check, deployment behavior or orchestrator metric changes in this delivery.

## Decision

The extension does not reach the target. The all-PR mean moved from 2.167 to 2.227 across the same 66 PRs as two PRs closed and four new heads appeared, and it stays above the strict "fewer than two" target. The movement is not monotone, and it cannot be attributed to the pre-commit rule: rule adoption and concurrent changes are unobserved, and the merged and open slices move differently. This closes the extension at the **2026-10-15 01:00:00 UTC** cutoff under the current requirement; the 13 right-censored PRs remain unfinished and their lifetime counts can still grow. Keep the current checks and rule unchanged.
