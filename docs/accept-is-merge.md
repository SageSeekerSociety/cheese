# Accepting a card is merging its pull request

Status: design, not yet implemented. Supersedes constraint 4 of
`docs/topics/两阶段采纳-PR迭代式实现.md` — see "The decision this reverses".

## The one sentence

A card is the platform's view of a pull request, and accepting it merges that
pull request. Nothing else in the platform decides whether a change is good.

## What is wrong today

The live path runs the loop backwards:

```
人点采纳  →  推分支、开 PR（用采纳人自己的 GitHub token）
          →  轮询 CI  →  全绿了系统自动合并  →  轮询部署  →  归档
```

Three things follow from that ordering, and all three are load-bearing problems:

1. **The reviewer approves an unverified change.** At the moment the button is
   clicked, no CI has run — the PR does not exist yet. The only signal is the
   pre-PR gate, which runs `check.sh --no-tests` because the host it runs on has
   no Postgres. So a green card today means "ruff and pyright passed"; not one
   test has run. The merge then happens later, unattended, whenever CI turns
   green. "采纳" means "go and try", not "I accept this change".
2. **Accepting requires a human's personal GitHub token.** The branch is pushed
   as the accepter. A member who has not linked GitHub cannot accept anything —
   this is not a missing convenience, it locks them out of the loop entirely.
   (Observed 2026-08-10: a member could not link her account for unrelated
   reasons and was, in effect, removed from the product.)
3. **The platform grew its own checker to fill the gap.** With no PR before
   acceptance there is nothing to read a check result from, so `review/gate.py`
   runs the project's `check_command` itself. Being an in-memory
   `asyncio.create_task` with no persistence, it produced the whole family of
   defects we have been paying for: orphaned cards, a `pending_gate` state with
   no exit that wedges the entire topic, startup and periodic sweepers, two
   timeout constants, and a `gate_failed` status that means two opposite things
   distinguished by a string prefix. 29 of 155 cards on dev (19%) died there.

A second mechanism already implements the right ordering — a card turning
`pending` opens its PR immediately using the **App's** installation token
(`review/pr_publish.py`, #195) — but it ships dark behind `accept_via_pr`,
which has been `False` since it landed and has never been switched on.

## Target

```
芝士 finishes, having run the checks itself (that is part of doing the work)
   → the platform opens a PR with the App's credential          no human token
      → the card is created as the view of that PR              diff + checks
         → CI runs whatever .github/workflows declares
            → the card's state mirrors the PR's
               → 采纳 = call the merge API                      only when green
                  → watch the deploy workflow → archive the topic
```

### Principles

- **The platform never runs checks.** A repository declares its checks in
  `.github/workflows`; the forge runs them and publishes the results. The
  platform reads them. `project.settings.check_command` is a third, private
  definition of "the checks" and is deleted.
- **The forge is the authority on mergeability.** Do not recompute "is this
  ready" from check runs — branch protection, required reviews and draft state
  all feed that answer and reimplementing them guarantees drift. Read check runs
  for display; call the merge API for truth and surface its refusal.
- **The agent has its own identity.** PRs are opened and merged with the App's
  installation token. No path depends on a member's personal token.
- **Accepting is one action with one meaning.** It merges. It does not start a
  process that merges later.

### Card states

The card mirrors the PR; GitHub is the source of truth and the poller
reconciles. A local state that disagrees with the forge is always wrong.

| state | meaning | leaves by |
|---|---|---|
| `opening` | PR is being created | PR created, or `open_failed` |
| `open_failed` | the PR could not be opened | terminal; 芝士 is told why |
| `checks_pending` | PR is open, checks have not concluded | checks conclude |
| `checks_failed` | a required check is red | 芝士 pushes a fix → `checks_pending` |
| `ready` | the forge says it is mergeable | 采纳 → `merged`; close → `closed` |
| `merged` | merged; the deploy workflow is being watched | deploy succeeds → topic archived |
| `closed` | PR closed without merging | terminal |

`pending_gate`, `gate_failed` and `conflict` are removed. Existing rows keep
their historical values — the migration does not rewrite what happened, it only
stops new cards from reaching the retired states.

### Auto-accept (human out of the loop)

A project setting. When a card reaches `ready`, the platform performs the merge
itself. That is the entire feature: the same action, taken by the machine.
It needs no separate approval path, because the meaning of accepting did not
change.

## Answers to the questions this raises

**One card, one PR.** More commits update the same PR — that is what PR
iteration is. A card never opens a second PR; if the PR is closed, the card is
closed and 芝士 files a new one.

**Someone acts on GitHub directly.** The poller reconciles rather than fights:
merged there → `merged` here, and the deploy watch proceeds; closed there →
`closed` here, the topic stays active and 芝士 is told.

**A project with no connected forge.** The card is created `open_failed` with
that reason, and 采纳 falls back to the local merge that exists today. This is
a degraded mode and is labelled as one on the card — not a silent second-class
path. A self-hosted checker that reports into the same interface is the way out,
and is out of scope here.

**Which checks must pass.** Whichever ones the forge requires. If a repository
configures no protection, the forge will merge a red PR and that is the
repository's decision to make, not the platform's to override.

## What is deleted

- `review/gate.py`, the `pending_gate` / `gate_failed` states, and
  `project.settings.check_command`
- the accept-time PR path that pushes with the accepter's personal token, and
  its dependency on a linked GitHub account
- the `accept_via_pr` flag — with one mechanism there is nothing to choose
- the gate orphan machinery from #286 (`gate_started_at`, the startup and
  periodic sweeps, `GATE_TIMEOUT_S` + `GATE_STALE_GRACE_S`, the
  abandoned-vs-failed prefix, the `void` exit). It correctly fixed a real
  deadlock; the deadlock only existed because the gate did.

## What is kept

`PrPollRunner`, `_nudge_pr_fix` (a red check wakes 芝士 to fix it, deduplicated
per commit), and the rule that a topic is archived only after the deploy
workflow reaches completed+success. That last one was decided on evidence —
merges whose deploy was silently cancelled — and the new ordering does not
touch it.

## The decision this reverses

`docs/topics/两阶段采纳-PR迭代式实现.md`, constraint 4, is recorded as settled:

> 内部闸门（`review/gate.py`，跑 ruff/pyright，不跑重的 pytest/e2e）保持不变,
> 继续在人点采纳按钮之前起作用 … 三层不互相替代（内部闸门 → PR CI → 部署）。

That constraint is sound under the ordering it was written for: with no PR
before acceptance, the gate was the only signal a reviewer could have. It
expires with the ordering rather than being overruled — once the PR is opened
before acceptance, real CI occupies the slot the gate was holding, and the third
layer is not removed but replaced by a stronger one that can actually run the
tests.

## Rollout

Each step is verifiable on dev before the next one starts, and no step deletes
anything the previous step has not already replaced.

1. **Converge the two mechanisms.** Keep PR-first on the App credential; remove
   the accept-time personal-token path. Until this lands, turning on
   `accept_via_pr` would run both at once — the existing design note warns about
   exactly that.
2. **Turn on PR-first on dev and drive a full round**: file a card → PR opens →
   CI runs → merge → deploy → topic archived. Nothing is deleted yet, so this is
   reversible by flipping the flag back.
3. **Retire the gate** once step 2 has run clean, taking #286's machinery with
   it and clearing `check_command`.
4. **Add auto-accept** as a project setting.
