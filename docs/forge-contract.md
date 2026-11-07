# What a forge has to provide, and what the agent is handed

Status: agreed 2026-09-19, not built. `docs/accept-is-merge.md` is the part that
already exists — a card is a pull request's view and accepting calls the merge
API — and this file is the layer under and around it: what any forge must supply
for that to work elsewhere than GitHub, and what an agent is given so it never
needs to reach for the merge button itself.

## The one sentence

The panel is where people work; a forge is storage, execution and record.

That holds for every project, not only for the ones whose users are engineers.
A document project gets versions, history and a diff without ever meeting the
words branch, pull request or merge (#1085), and a code project's reviewer stays
in the panel too — not because GitHub's page is bad, but because a product whose
review surface is somebody else's website cannot decide what review means.

## The contract

Six slots. A forge is supported when all six are filled; a second forge is a
second implementation of exactly these and nothing more.

1. **Git authentication.** How a machine authenticates a fetch and a push.
   Short-lived and minted on demand — never a value the agent stores. For git
   that is a credential helper the platform answers; for a CLI that reads an
   environment variable, a wrapper that mints and execs. The rule the agent is
   given is one line: use it at the point of use, never export it.
2. **API authentication.** The same, for the calls that are not git.
3. **The acceptance operations.** Seven: open a pull request, rewrite its title
   and body, take it out of draft, read the checks on its head, merge it, close
   it, comment on it. This is the whole forge-facing surface of 递卡 and 采纳.
4. **Naming.** Pull request or merge request; the concepts map one to one —
   one task, one branch, one request.
5. **Event delivery.** How this deployment learns that a check finished or a
   request was merged. Preferred: the forge's webhooks, received by one public
   endpoint we operate and forwarded to each deployment over the connection the
   deployment already opened outward — the shape the device connector and
   `wss://…/llm/tunnel` already use, because a deployment with no inbound route
   is the normal case, not the exception. GitHub's own `gh webhook forward` is
   the same idea and is documented as test-only, one user per repository, so it
   is not the mechanism. **Polling stays regardless**, at a slow interval, with
   its job changed from noticing to reconciling: webhooks are lost by restarts,
   dropped tunnels and expired retries, and a state that quietly diverges is
   worse than one that arrives late.
6. **Code delivery.** How a machine gets the code and returns it. A machine that
   can reach the forge goes straight there; a machine that cannot reaches the
   platform, which is the only route it has. See #1280 for that path.

## What the agent is handed

**The product's own verbs, as tools.** 递卡 is the one that matters: this task
is ready for someone to accept, here is what to put on the card. A tool is in
the list the agent reads before it acts, which a warning printed after the fact
is not, and its description is where the boundary belongs: whether the work is
accepted is the project's decision, and an agent that merges its own pull
request has accepted its own work — the rule `not_its_own` already states for a
session approving its own tool calls.

Alongside it, the tool that stops the agent from going looking: where this task
stands — accepted or not, what review said, which check is red.

**Everything else about the forge stays raw.** `gh` is in the sandbox image on
purpose, and the long tail — a failing job's log, a review thread, a commit's
checks — is where an agent earns its keep. Wrapping all of it would trade that
away for an abstraction whose second implementation does not exist yet. The
token it mints carries what the platform's app was granted on that repository,
deliberately, because the people who can put a task on a machine are the
project's own.

## What follows for the panel

- **Review happens in the panel** for every project type. What is written there
  goes to the forge as well, so the record is complete for anyone reading the
  pull request; what an outside reviewer writes on the forge appears in the
  panel marked as coming from it. The two are not peers: one is where deciding
  happens, the other is where it is recorded.
- **A refusal from the forge is translated.** 「还差一位评审」, not a 405 with
  GitHub's own wording.
- **The diff's unit is the deliverable, not the branch** (#1085): what changed
  between this version of that report and the last one.

## Attribution

A commit is authored by the agent's own identity (#1187), carries a
`Co-authored-by` for the person who asked, and the pull request's body names the
room and the task. Cursor's version of this is on by default and adds
`Co-authored-by: Cursor Agent <cursoragent@cursor.com>`; the complaint that
follows it is worth learning from too — that identity lands in a repository's
Contributors list and stays there even after the history is rewritten. So the
switch exists from the start, per project, with a deployment-level default.

## Still open

- **A project with no forge at all.** Acceptance has nothing to merge. Either
  the platform runs a forge of its own for them or acceptance means something
  else there; that is a product decision (#1280).
- **Dependent tasks.** A task whose base is another task's branch is a stacked
  request. Retargeting a child when its parent merges already exists (#1213);
  what is not decided is the depth we support and what happens to the child when
  the parent is rejected.
- **Which verbs beyond 递卡** deserve to be tools. The test is whether a second
  forge would have to implement it, not whether it is convenient.
