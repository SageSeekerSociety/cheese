---
paths:
  - "docs/manual/dev/**"
---

# Metric snapshots in docs/manual/dev/

Read [`../../AGENTS.md`](../../AGENTS.md) first.

For published main-branch numbers, use a matching main run of
`.github/workflows/arch-metrics.yml` and state which revision it measured. The
workflow collects every three hours rather than on every merge, so a revision no
run has covered is collected by dispatching it on main while main is at that
revision (Actions → Architecture metrics → Run workflow). Read its `arch-metrics`
artifact and job summary.
A local branch measurement answers a different question and must be labelled so.

When merging main, keep main's metric snapshots in `arch-metrics.md`, `scenes.md`,
`backend-app.md` and `frontend.md`, then reapply this branch's intentional prose
changes. Do not independently regenerate the snapshots just to settle a merge:
parallel snapshots conflict without changing the code's merge eligibility.

Read live debt from the owning baselines or checker output. Do not copy a fixed
number of checks or exceptions into a new instruction: frontend JSON baselines,
deferred-import JSON and unit-test ledgers are different accounts, and their
counts change independently. Backend contract debt is measured from the tree at
the merge base on every check run.
