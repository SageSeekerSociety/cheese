# Accepting a card is merging its pull request

Status: partly implemented, and one rule below was overruled after it was
written. PR-first is on (`accept_via_pr` defaults to `True`,
`core/config.py:470`) and the gate is retired (`review/gate.py` is no longer
dispatched). Not done: `project.settings.check_command` still exists, and the
card states in "Card states" were never built — the shipped path runs
`pending → pr_open → accepted` (`review/models.py`, `AcceptStatus`). Overruled:
the deploy-gated archive, removed by #206 — see "What is kept".

Supersedes constraint 4 of `docs/topics/两阶段采纳-PR迭代式实现.md` — see "The
decision this reverses".

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
which was `False` when this was written. It has since been switched on
(`core/config.py:470`).

## Target

```
芝士 finishes, having run the checks itself (that is part of doing the work)
   → the platform opens a PR with the App's credential          no human token
      → the card is created as the view of that PR              diff + checks
         → CI runs whatever .github/workflows declares
            → the card's state mirrors the PR's
               → 采纳 = call the merge API                      only when green
                  → the topic is marked delivered, and stays active
```

> **Update (2026-08-15).** "only when green" is now enforced by the platform,
> not left to whoever is looking at the card: 采纳 on the App forge marks the
> card `pr_open` and the poller calls the merge API once the forge's checks are
> actually green. The escape hatch for merging a red PR is an explicit, signed
> action (`POST /accept-cards/{id}/merge-anyway`). See
> [`docs/topics/App采纳等CI再合.md`](topics/App采纳等CI再合.md) — including why
> this is still "mirror, don't gate" and not a revival of `review/gate.py`.
>
> **Update (2026-08-16, #468).** The poller now also carries the two guarantees
> GitHub's branch protection would give if the plan allowed it (free-plan
> private repos 403 on the protection API): a **required-check roster**
> (`accept_required_check_names`, default `test`) — a check that has not
> *reported* on the head SHA blocks the merge, closing the window where "no
> failures" meant "nothing ran yet" — and **strict up-to-date**: a head that is
> behind or diverged from `main` is rebased via the update-branch API (capped at
> three rounds per card) and re-checked before merging, so green earned against
> an older main is never treated as green against today's.
>
> **Correction (2026-08-16, same day).** The roster shipped as bare names, which
> made every entry unconditional — and `test` lives in a workflow that only
> triggers on `backend/**`, so on a frontend-only PR it never appears at all.
> Three fully-green PRs (#483/#485/#486) waited until a human merged them by
> hand. A roster entry now carries the diff scope that makes it required
> (`test:backend/**;.github/workflows/test.yml`), and the platform asks GitHub
> for the PR's file list — only when something is missing, so the steady-state
> poll costs the same as before. Two rules keep it a valve rather than a
> formality: an unresolvable scope (compare truncated, base branch unreadable)
> keeps the check required, and a check still missing after
> `accept_required_check_grace_minutes` (30) stops waiting and asks a human —
> **the timeout's exit is a person, never an auto-merge**. Without that backstop
> a renamed workflow, a disabled one, or an Actions billing lapse (this org had
> one on 2026-08-13) hangs a card forever with nobody told.

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
- **A merge ends the change, not the room** (#442 decision 1, 2026-08-17). It
  stamps `accepted_by`/`accepted_at` on the topic and stops there: the topic
  stays `active`, its sandbox is not torn down, and archiving is a separate
  thing a person does. What the merge *does* freeze is delivery — the accepted
  card blocks a second card on the same topic, because that topic's branch is
  already in `main` and a PR from it would carry no commits. A follow-up change
  is a new piece of work with a branch cut from today's `main` (eval A4).

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
| `merged` | merged | terminal for the card; the topic stays active |
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
merged there → `merged` here; closed there → `closed` here, the topic stays
active and 芝士 is told.

**A project with no connected forge.** The platform is its forge (#363): 采纳
merges the topic branch into the platform's own repo, and the change stays
there — nothing is pushed to any remote, because the platform holds no
credential of its own for one (the App's tokens belong to bound projects). The
card says so in so many words (`PLATFORM_FORGE_NOTE`, `review/forge.py`), so an
unbound accept and a bound project that skipped its PR never look alike. This
is not a degraded mode; it is a repository with no CI configured, where
accepting is a human decision.

**Which checks must pass.** In principle, whichever ones the forge requires —
but on a free-plan private repository the forge cannot be *told* to require any
(the branch-protection API answers 403 "Upgrade to Pro"), so with the forge
alone a red or unstarted check never blocks a merge. The accept entrance
therefore carries that tier itself: the poller merges only when every name in
`accept_required_check_names` that this diff can actually trigger has reported
green on a head that is current with `main` (#468). "That this diff can
trigger" is not a loophole but the difference between a valve and a deadlock:
GitHub workflows carry their own `paths:` filters, so a check the change cannot
possibly start is absent for a reason, and a roster that cannot tell those two
absences apart blocks green PRs forever instead of blocking untested ones.
The boundary is deliberate — **these semantics guard the accept
entrance only**. A human merging directly on GitHub bypasses them, and the
platform's answer to that stays "mirror, don't gate": the poller reconciles the
card to whatever the forge says happened. One entrance is governed; the forge
itself is not re-implemented.

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

The periodic pr poll (`SchedulerService.poll_open_prs`) and `_nudge_pr_fix`
(a red check wakes 芝士 to fix it, deduplicated per commit).

This paragraph used to also keep the rule that a topic is archived only after
the deploy workflow reaches completed+success. **That rule was removed by #206
and is not kept.** Merged is a fact about git that holds for every project,
while "deployed" is a per-project ops concept the platform was in no position to
define; cards then waited on deploy runs that were sometimes never created at
all — three real merges on main on 2026-08-11 produced zero runs, which is a
deadlock rather than a safeguard. The shipped code archives on merge with no
deploy wait: `review/services.py::_finish_pr_accept`. Watching the deploy is
real work and it keeps a home — the webhook primitive (`POST /webhooks/{topic_id}`,
`api/routes/webhooks.py`) lets a pipeline post its outcome into the topic, and
#190's ops room is where that judgment belongs.

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
   CI runs → merge → topic archived. Nothing is deleted yet, so this is
   reversible by flipping the flag back.
3. **Retire the gate** once step 2 has run clean, taking #286's machinery with
   it and clearing `check_command`.
4. **Add auto-accept** as a project setting.
