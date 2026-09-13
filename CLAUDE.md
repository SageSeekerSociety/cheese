# Project Conventions

**This file states principles. It never states how things currently are.**

A fact about the present is a trap on a delay: the moment someone fixes what you described, your sentence is still sitting here, still reading as current, and the next agent believes it. Anything that would become wrong *because somebody did good work* belongs in `docs/` or in an issue — not in the file that loads on every single turn. The same goes for anything a tool already enforces: a linter that goes red teaches the rule better than a paragraph, and it cannot fall out of date.

| This kind of thing | Lives in |
|---|---|
| A principle you must hold to while changing code, that **no tool will stop you from violating** | this file |
| A design still being argued | issues |
| How things currently are, and the long explanations | `docs/` |
| A pitfall that only applies while touching one area | `.claude/rules/` |
| Anything equally true of **any** repo we host | the platform skill |

## This repo does not adapt to the platform

Cheese hosts other people's repositories, and **a repository must never have to change in order to be hosted** — no config file, no wrapper script, no paragraph in its CLAUDE.md explaining our sandbox. Every line a repo is asked to add is a reason not to adopt us, and for the repos we do not control, asking is not on the table.

So this file describes **this project** and nothing else. Anything equally true of a hosted repo goes in `backend/sandbox/skills/cheese/SKILL.md`, which reaches all of them at once; the same words here would fix it for us alone, and demonstrate the very adaptation we promise nobody has to make.

We are the easiest repo in the world to get this wrong in, being both the platform and one of the repos it hosts — so `check-repo-rules.sh` guards it. The tell, when you are unsure: **a rule that is false when you are NOT in a sandbox is leaked platform knowledge.**

## Production changes to this codebase go through CI/CD

Apply this rule to the Cheese codebase in this repository. Treat its shared live
deployment as production even when it is named dev.
Changes to this repository's code, deployment configuration and database schema
there must go through normal CI/CD: merge through the required checks, then
deploy the resulting main commit or approved release with its pipeline-built or
pinned images. Passing feature-branch CI does not authorize deploying that branch
to production, even by manually triggering a GitHub workflow.

Normal CI/CD means this project's established production delivery pipeline,
including its release gates, deployment procedure and health verification.
Building an artifact in CI and handing it to a person or agent for manual
installation is not that pipeline; neither is an ad hoc deployment script or
workflow created to bypass it.

For this codebase's production deployment, do not substitute custom Docker images,
local builds or retagged candidates, hot-edit containers, mount candidate source
over deployed code, or run deployments or database migrations by hand to bypass
CI/CD. Image recipes and dependency changes follow the same release path.
Recovery also uses normal CI/CD with database-compatible artifacts.

SSH diagnosis is allowed. For independently maintained projects and services
outside this repository (including MicroCloud and the metering proxy), follow
their own deployment procedures and authorization rules; do not require them to
adopt Cheese's CI/CD or ask for an exception to this Cheese-only rule. Separate
test environments are also outside this restriction. Existing safety and
authorization rules still apply. Run pre-merge
experiments there without changing this codebase's production deployment or data.

## Read the issues before the docs, and clean the docs when a design lands

**The design that is still being argued lives in issues.** Whatever `docs/` says about it was written before the argument finished, so starting there means implementing something already superseded — and nothing in the file will tell you.

**Then, when a design does land, delete every sentence it invalidated — in the same PR.** A stale sentence reads exactly like a current one; that is the whole danger. A follow-up is a promise while the trap stays live, and annotating a section "outdated" leaves the trap in place while helping only whoever reads that far. Delete the claim; git history keeps it. Then close the issue with the reasoning, so the next agent finds a conclusion instead of re-arguing it.

## We are still building this — do not preserve what is already decided against

Once a decision is made, carry it out completely. Do not leave behind the shape
being replaced, a note about when it expires, a compatibility path, a
deprecation marker, a dual-write, a flag keeping the old branch reachable, or a
doc section explaining what the old way was. Every one of those is a cost a
mature system pays to protect the users it already has. We do not have them yet,
so it buys nothing and leaves more to unwind — and each one is a place the next
reader can mistake for something still in use.

Retiring something means deleting its code, and adopting a replacement means
deleting what it replaced. Short of that the path stays wired: what nobody can
select is still what an unconfigured case runs, and a new shape can be
contracted, documented and never reached while the old call site serves every
request. Both read as done to anyone checking the surface, which is what makes
them worth naming — the change is finished when the code that chooses changes,
so check there and not in the menu, the contract or the doc.

The corollary is that a thing must be judged by what it does now, not by the
reason it was created. Mechanisms drift away from their purpose while keeping
their name: a check written after an incident points at where that incident
surfaced, and the code that causes it moves on. Look at what it touches today
before deciding it is worth keeping.

## Assume other agents are working right now

Not hypothetically — concurrently, in this repo, on adjacent files. Before starting a fix, look for someone already fixing it in an open PR. Before handing work off, review **every path your change touches**; a cache directory in that list is a stop sign.

So never `git stash`. The stash is one stack for the whole repository, shared by every worktree, and nothing marks which worktree an entry came from — a pop in yours applies somebody else's uncommitted work and drops it from under them, silently, with no error on either side. Commit instead — the worktree you are in is already the second checkout a stash would have bought you, and `git reset --soft HEAD~1` hands the staged state back when you want it.

Not stashing by hand is not enough: with `autoStash` on, a plain `git pull` is a stash push and pop, and many people carry it in their global config. `task setup` turns it off for this repository; run `task git:config` if you cloned before that existed.

## Test behaviour, never the implementation

Write functional tests. Do not read the source to work out what to assert — a test derived from the implementation passes by construction and proves nothing, including after the implementation breaks.

## Report real bugs, not theoretical ones

When auditing, a finding needs a way to actually happen: crash, corruption, security hole, wrong result. Style opinions and "this could in principle" are noise that buries the real ones.
