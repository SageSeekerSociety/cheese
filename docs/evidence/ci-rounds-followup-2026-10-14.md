# Post-rule CI-head follow-up, 2026-10-14

The first post-rule cohort has **66 PRs and 143 distinct CI heads**, averaging **2.167 heads per PR**, versus the baseline's 2.327. The observed mean is lower by 0.160 heads per PR (6.89%), but remains above the strict target of fewer than two. This short observational comparison does not establish the pre-commit rule's causal effect.

## Population and definition

[PR #3702](https://github.com/SageSeekerSociety/cheese/pull/3702) merged the pre-commit quick-check instruction at **2026-10-14 11:58:55 UTC**, commit `d0761ba19ce03d6f98e344d726694103dc920adf`. The follow-up includes PRs created in the half-open interval **2026-10-14 11:58:55 UTC through 23:34:57 UTC**. The run/check snapshot was observed at **23:38:11 UTC**, 3 minutes 14 seconds after cutoff. The window spans 11 hours 36 minutes 2 seconds.

The definition is unchanged from the [baseline](ci-rounds-2026-10-14.md): one distinct PR head SHA with a `pull_request` run of `required-ci.yml`, from PR creation until closure or cutoff. Draft/full runs on the same head, reruns, return to an old SHA, and merge-group runs do not add heads. A full judgement requires the exact check name `CI required`; draft quick green is not full green.

The PR-created cohort excludes pre-existing PRs that pushed after the rule merged. Birth after merge is not evidence that the author loaded or followed the new instruction. No required check, deployment behavior, or existing orchestrator metric changes in this delivery.

## Before and after

| State at cutoff | Baseline PRs | Baseline heads | Baseline mean | Follow-up PRs | Follow-up heads | Follow-up mean |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| All | 1,462 | 3,402 | 2.327 | 66 | 143 | 2.167 |
| Merged | 1,366 | 3,173 | 2.323 | 50 | 100 | 2.000 |
| Open | 55 | 162 | 2.945 | 15 | 43 | 2.867 |
| Closed unmerged | 41 | 67 | 1.634 | 1 | 0 | 0.000 |

The merged-only mean is lower by 0.323 heads per PR (13.90%), but exactly two is not fewer than two. Fifteen follow-up PRs remain right-censored, compared with 55 in the baseline (22.73% versus 3.76%). Their eventual lifetime counts can increase. One zero-run PR stays in the follow-up denominator, as the baseline's eight zero-run PRs did.

The follow-up median is two (baseline one); 35 of 66 PRs have at least two heads, compared with 707 of 1,462 (53.03% versus 48.36%). A lower mean is therefore not a uniform shift across PRs. Observed exposure from creation to closure/cutoff ranges from 0.177 to 8.995 hours, with a median of 1.725; the baseline median is 0.936 hours and maximum 284.338 hours. State composition and lifetime exposure differ.

| Follow-up heads per PR | PRs |
| --- | ---: |
| 0 | 1 |
| 1 | 30 |
| 2 | 16 |
| 3 | 8 |
| 4 | 5 |
| 5 | 2 |
| 6 | 1 |
| 7 | 3 |

## Equal-age sensitivity check

For age H, eligibility requires PR creation at least H hours before its own cohort cutoff. Count distinct heads before `min(closure, creation + H hours)`. PRs that closed before H remain included: excluding them would select for long-lived PRs. The interval is half-open, and younger PRs are excluded from both denominators.

| First H hours | Baseline PRs | Baseline heads | Baseline mean | Follow-up PRs | Follow-up heads | Follow-up mean | Change |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 2 | 1,457 | 2,723 | 1.869 | 62 | 121 | 1.952 | +4.43% |
| 4 | 1,446 | 2,937 | 2.031 | 56 | 119 | 2.125 | +4.62% |
| 6 | 1,438 | 3,026 | 2.104 | 44 | 79 | 1.795 | −14.68% |
| 8 | 1,432 | 3,085 | 2.154 | 26 | 46 | 1.769 | −17.88% |

The direction changes with H: the two- and four-hour means rise, while six- and eight-hour means fall. Eligibility changes the follow-up population from 62 to 26 PRs, selecting earlier births at larger H. A six-hour decrease alone would hide this sensitivity. These are first-H-hours statistics, not the task's completed-lifetime target.

The six-hour subset excludes 24 younger baseline PRs and 22 younger follow-up PRs. Its median rises from one to 1.5; 22 of 44 follow-up PRs have at least two heads, versus 656 of 1,438 baseline PRs. The short window, changing eligibility, PR scope, publication habits, time-of-day composition, concurrent changes, and unverified rule adoption prevent isolating the rule's effect. No causal or statistically established effect is claimed.

## Per-PR evidence and reproduction

[The per-PR CSV](ci-rounds-followup-2026-10-14.csv) lists all 66 PRs, birth time, state at cutoff, right-censoring, distinct-head rounds, exposure, full-judged heads, six-hour eligibility/count, and each head's first observed run timestamp. Summing its lifetime count yields 143. Its 44 eligible six-hour rows sum to 79. Full judgements cover 104 of the 143 heads before cutoff; heads without one are still counted as workflow-triggered heads, never labelled full green.

The follow-up archive contains both frozen snapshots, both standard reports, the comparison output, per-PR JSON/CSV, the offline comparison script, and checksums. It preserves the baseline observation rather than recollecting it at a later date.

```sh
python3 .claude/scripts/ci_rounds.py ci-rounds-followup-20261014-233457.json \
  --output reproduced-followup-report.json
python3 compare-ci-rounds-followup.py ci-rounds-evidence-full-20261014.json \
  ci-rounds-followup-20261014-233457.json reproduced-comparison
python3 -m unittest discover -s .claude/scripts -p 'test_ci_rounds.py'
```

The comparison script is in the delivered archive, not an additional production metric. New live observations must use a fresh cache:

```sh
python3 .claude/scripts/ci_rounds_collect.py \
  --since 2026-10-14T11:58:55Z --until 2026-10-14T23:34:57Z \
  --cache /tmp/ci-rounds-followup-fresh-cache \
  --output /tmp/ci-rounds-followup-snapshot.json --only-multi-head
```

Recollection can change current-attempt checks and PR closure evidence; use the archived snapshot to reproduce this report. Raw multi-head timelines were fetched for 36 PRs; lifetime filtering leaves 35 multi-head PRs. Historical check attempts are incomplete. Those limitations affect attribution and promotion evidence, not the distinct-head calculation from the saved run list.

## Decision

The sample is sufficient to report a descriptive difference: 2.327 to 2.167 across 66 follow-up PRs. The all-PR target is not met, and a reduction caused by the rule is not established. Keep the current checks and rule unchanged; revisit this same cohort's unfinished PRs with a later cutoff rather than claiming success or adding another gate from these results.
