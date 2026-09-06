# PR-based accept (采纳 PR 化) — design

Status: approved direction (#188 §5.1), this doc fixes the mechanics. Implementation
is flag-gated and ships dark.

## Why (recap of #188)

Accept today is a platform-local `git merge --no-ff` + direct push to upstream main.
Three real costs, all observed in dogfooding:

1. The only pre-merge gate is the platform gate, and its dogfood config is
   `check.sh --no-tests` — ruff + pyright, zero tests. The full test suite lives in
   the PR-triggered `test.yml`, which a direct push to main never runs.
2. Direct pushes leave no PR record — no audit trail, no place for checks to hang.
3. CI runs *after* the code is already on main; a red build is discovered by the
   deploy reconciler (or nobody), not by the accepter.

PR-based accept inverts this: submitting an accept card opens a real PR, the full CI
runs while the card waits for the human, and 采纳 = merging that PR via the GitHub
API. GitHub's machinery (checks, audit, conflict detection) comes for free.

## Shape

```
递卡 → gate (unchanged) → card pending
                          └─ [flag on] push topic/<8hex> to upstream + open PR
                             card records pr_number / pr_url; CI runs on the PR
人采纳 → card has PR?
          ├─ yes: snapshot worktree → re-push branch → merge PR via API
          │        → sync local main DOWN from upstream → archive topic
          └─ no:  old path unchanged (local merge)
```

### Decisions

- **Flag**: `accept_via_pr: bool = False` in `Settings`. Submission-side only.
  The accept side does not read the flag — it dispatches on whether the card
  carries a `pr_number`. Mixed populations (cards submitted before/after a flag
  flip) and rollback are therefore well-defined: every card is self-describing.
- **PR opening is best-effort enrichment.** It runs fire-and-forget after the card
  turns `pending` (gate green, or born pending when the project has no gate) —
  same dispatch pattern as `gate.py`. If the push or PR creation fails, the card
  simply stays PR-less and accept falls back to the old path; the failure is
  logged and noted on the card. GitHub being down must never block acceptance.
- **Eligibility**: flag on AND GitHub App configured AND the project's upstream
  remote parses as `github.com/<owner>/<repo>`. Phase 1 assumes the single
  configured installation (the dogfood repo); per-project installations are #192.
- **Auth**: installation tokens minted server-side with `contents:write` +
  `pull_requests:write` (`GitHubAppTokens.write_token()`, cached in its own slot,
  separate from the full-grant mint an agent gets). This mint never leaves the
  backend process; the git push wires the token through an in-memory credential
  helper (env var), never argv, never disk.
- **Branch on GitHub**: same name as local, `topic/<8hex>`. Re-pushed
  (`--force-with-lease`) at accept time after the pre-merge snapshot, so
  last-minute worktree edits and conflict fixes are what actually merges.
- **Merge method**: merge commit (matches existing history), title
  `采纳 topic/<8hex> → <target> (#<pr>)`, body records reviewer handle and
  routing reason. Committer/author become the App bot — the who-wrote/who-approved
  trailer story is #189, deliberately not solved here.
- **Checks are displayed, not enforced.** The accept endpoint does not refuse on
  red/pending checks in phase 1 — the current path runs CI *after* merge, so
  showing checks *before* accept is already a strict improvement, and enforcement
  belongs to branch protection once the old direct-push path is retired (branch
  protection would break it today).
- **Conflicts**: GitHub refuses the merge (405) → card `conflict` + the existing
  conflict machinery (`prepare_conflict_resolution` + agent summon) is reused
  unchanged; the fix lands in the worktree, re-accept re-pushes the branch and
  retries the API merge.
- **After merge**: `sync_upstream` runs to fast-forward the platform's local
  main to the merge commit. Card note: `已通过 PR #<n> 合并到 <target>`.
- **Data model**: `accept_cards` gains nullable `pr_number: int`, `pr_url: str`.
  No new statuses.
- **Surfaces**: `topic_status` card snapshot exposes the PR fields; new
  `GET /api/topics/{id}/pr-checks` proxies live check-run state (read token);
  frontend card shows PR link + checks; `cheese` CLI `_format_status` prints them
  (the agent sees its own PR's CI without leaving the sandbox).

## Out of scope (tracked elsewhere)

- PR-event wakeup (checks failed / conflict / merged → summon agent): the polling
  card in #188 §5.3 — this design only *displays* state when someone looks.
- Gate demotion to fast pre-check (#188 §六.6): gate behavior untouched here.
- Credit trailers (#189), per-project installations (#192), branch protection.

## Rollout

1. Ship dark (flag off). Unit + integration tests mock GitHub.
2. Flip on dev, submit a real card end-to-end, verify PR → checks → API merge →
   sync-down → archive, then announce in #188.
3. Old path stays until the flag has survived real dogfooding; retiring it (and
   adding branch protection) is a separate decision.

## Rollback

Flip the flag off: new cards revert to the old path instantly; in-flight PR cards
still accept correctly via their `pr_number`. No migration to undo (columns are
nullable and inert).
