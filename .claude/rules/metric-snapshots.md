---
paths:
  - "docs/manual/dev/**"
---

# Metric snapshots in docs/manual/dev/

Read [`../../AGENTS.md`](../../AGENTS.md) first.

For published main-branch numbers, use a matching main run of
`.github/workflows/arch-metrics.yml` and state which revision it measured. Its
`arch-metrics` and `ratchet-snapshot` are separate artifacts; check for a
`ratchet-snapshot.failed.txt` instead of treating a failed collection as zero.
A local branch measurement answers a different question and must be labelled so.

When merging main, keep main's metric snapshots in `arch-metrics.md`, `scenes.md`,
`backend-app.md` and `frontend.md`, then reapply this branch's intentional prose
changes. Do not independently regenerate the snapshots just to settle a merge:
parallel snapshots conflict without changing the code's merge eligibility.

Read live debt from the owning baselines or the checker output, with
`.claude/scripts/ratchet-snapshot.py`'s `CHECKS` as the inventory. Do not copy a
fixed number of checks or exceptions into a new instruction: the frontend JSON
baselines, deferred-import JSON and unit-test ledgers are different accounts, and
their counts change independently. The backend contracts are a third shape
again — their debt is not written down anywhere, the check measures it from the
tree at the merge base on every run, so the number to quote is the `frozen` one
in that commit's ratchet snapshot.
