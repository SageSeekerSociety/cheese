# Accepting a card is merging its pull request

Status: implemented (#296 → #422 → #718). The gate is retired, the personal-token
path is deleted, and since #718 accepting merges **on the spot** — #422's
authorize-then-poll default is withdrawn, with its signed escape hatch kept.

## The one sentence

A card is the platform's view of a pull request, and accepting it calls the
merge API — for the exact commit the reviewer was looking at. "Only merge when
green" is enforced by branch-protection rules configured per project, the way
GitHub's own protection page works.

## The shape

```
芝士 makes the batch's first commit
   → the platform opens a DRAFT PR for that batch                有东西就有 PR
      → CI runs whatever .github/workflows declares
         → 芝士 finishes, having run the checks itself
            → 递卡: the PR leaves draft, the card is its view    diff + checks
               → the card mirrors the PR's merge state          CLEAN/UNSTABLE/…
                  → 采纳 = call the merge API, sha=the head shown right now
                     → the topic is marked delivered, and stays active
```

The PR opens at the batch's FIRST COMMIT, not when a card is filed (#718 拍板①):
draft is GitHub's word for 进行中, and having a PR from the start is what lets CI
run and a reviewer look before anybody is asked to accept anything. `cheese
ready` takes it out of draft without filing a card, for work worth showing that
nobody is being asked to accept yet; 递卡 does the same flip as a side effect,
because asking someone to look at it is what 递卡 means.

The platform never sees that first commit — a 分身 commits inside the shared
worktree, with no push and no webhook — so it is OBSERVED rather than hooked:
`pr_publish.sweep_draft_prs` runs on the PR poller's clock and looks for a batch
whose branch is ahead of main. The cost is at most one tick of latency plus the
time this pass spends on the batches ahead of it, and the reason that is
acceptable is written where the sweep is.

The card's state IS the merge state (`merge_state.compute_merge_state`, mirrored
by the poller onto `AcceptCard.merge_state`): `CLEAN`, `UNSTABLE`, `BLOCKED`,
`BEHIND`, `DIRTY`. Next to it the card annotates whose move it is (CI / 芝士 /
平台 / 人), and the poller sends that as an event to whoever is doing the work,
deduplicated by (head sha, state, detail) through the card's `nudge_state`
ledger.

### Principles

- **The platform never runs checks.** A repository declares its checks in
  `.github/workflows`; the forge runs them and publishes the results. The
  platform reads them.
- **GitHub 能判定的听 GitHub，判定不了的平台按同一套规则补位。** A bound project
  whose repo has GitHub-side protection gets the passthrough: the card shows
  GitHub's verdict, the click calls the merge API, and a 405 is the answer. A
  repo where GitHub cannot enforce anything (free-plan private repos 403 every
  protection endpoint, and `UNSTABLE` means the merge API answers 200 with red
  checks) gets the same rules from the platform, configured per project
  (`branch_protection` in project settings: required checks with path scopes,
  strict up-to-date, dismiss-stale, auto-merge, the override roster).
- **The human merges what the human saw**, and "what the human saw" is what the
  BROWSER declares, not what the card row happens to say now. Accept,
  merge-anyway and the auto-merge arm each carry the `merge_state.head_sha`
  their page rendered; the server refuses (422) unless that is still the card's
  head, and merges with the sha the request carried. Three guards, in the order
  a push can slip through them: the declared sha catches a poll that moved the
  card between render and click; re-reading the PR catches a push the poll has
  not seen yet; the merge API's own sha parameter answers 409 for a push landing
  during the call. New commits also dismiss existing approvals by default
  (`dismiss_stale`, inverted from GitHub's default because the pusher here is
  芝士 holding App write credentials, not a trusted human) — but that clause
  protects the votes, not the click, which is why the declared sha exists.
- **The agent has its own identity.** PRs are opened and merged with the App's
  installation token. No path depends on a member's personal token.
- **Accepting is one action with one meaning.** It merges, now. Merging later
  exists only as the reviewer's own explicit choice: the per-card auto-merge
  arm (`auto_merge_allowed` projects), which merges with the armer's name once
  the rules hold — and is disarmed by the same dismiss-stale clause.
- **Red-but-merge is a signed human act.** `POST /accept-cards/{id}/merge-anyway`,
  admitted by the project's override roster (unconfigured = owner + leads),
  recording who, when, the check state at that moment, and why.
- **A merge ends the change, not the room** (#442 decision 1). It stamps
  `accepted_by`/`accepted_at`, syncs the platform's local base down, and stops
  there: the topic stays `active`, archiving is a person's separate act.

### The poller does three things

`SchedulerService.poll_open_prs` → `AcceptService.advance_pr_card`, for every
pending card riding a PR on an active topic:

1. **Mirror** the merge state onto the card (and reconcile: merged on GitHub →
   accepted here; closed unmerged → say so and idle; a moved head → dismiss
   stale approvals and the auto-merge arm).
2. **Send the events** the 「谁的活」 table names: red checks and conflicts to
   the agent (with logs and how to fetch more), `BEHIND` handled by the
   platform itself (update-branch, capped), a required check missing past the
   grace to a human, `CLEAN` to the reviewer.
3. **Merge an armed card** once the rules hold — same sha guard, the armer's
   name on the decision.

Authorization-era machinery — `pr_open` as the accept's default result, the
frozen `pr_authorized_sha` baseline, drift comparison, the three exemptions,
the platform-wide required-check roster and its grace config — is deleted; the
per-project rules replaced all of it.

## Answers to the questions this raises

**One BATCH, one PR.** The PR belongs to the tree (`work_trees.pr_number`),
which is what「一棵树 = 一个分支 = 一个 PR = 一批活」has meant all along; the card
adopts it rather than opening a second. More commits update the same PR — that is
what PR iteration is. The workspace's commits reach the PR on demand (`cheese
push-fix`); the poller never pushes on a timer (a 60-second pusher raced the
agent and cancelled its own CI runs).

**Someone acts on GitHub directly.** The poller reconciles rather than fights:
merged there → accepted here; closed there → the card says so, the topic stays
active, and a human reopens or voids.

**A project with no connected forge.** The platform is its forge (#363): 采纳
merges the topic branch into the platform's own repo, and the change stays
there — nothing is pushed to any remote. The card says so in so many words
(`PLATFORM_FORGE_NOTE`, `review/forge.py`). This is not a degraded mode; it is
a repository with no CI configured, where accepting is a human decision.

**Which checks must pass.** Whichever ones the project's own branch protection
names (`required_checks`, each optionally scoped to the paths that make it
required — a workflow with a `paths:` filter is legitimately absent on a diff
it cannot trigger, #470). The roster defaults to empty: requiring hosted repos
to add checks we name was rejected in #640. This repo configures
`test:backend/**`. A required check that has not reported, or has reported and
is still running, is `BLOCKED` — 没有结论不是通过 (#465/#468), and a check that
has not finished has no conclusion. One missing past the grace goes to a human,
never to an auto-merge. `UNSTABLE` therefore means exactly one thing: some
check is not green and none of those are required — which is why it, and only
it, joins `CLEAN` in what the accept gate and the auto-merge arm will merge.
