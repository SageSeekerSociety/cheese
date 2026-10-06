# Project Conventions

This file holds principles that no tool enforces. It never describes how things currently are: a sentence about the present turns false the moment someone fixes what it describes, and keeps reading as current. Whatever a linter or test already enforces does not belong here either.

| This kind of thing | Lives in |
|---|---|
| A principle to hold while changing code, that no tool will stop you from violating | this file |
| A design still being argued | issues |
| How things currently are, and the long explanations | `docs/` |
| A pitfall that only applies while touching one area | `.claude/rules/` |
| Anything equally true of any repo we host | the platform skill |

## This repo does not adapt to the platform

Cheese hosts other people's repositories, and a repository must never have to change in order to be hosted: no config file, no wrapper script, no paragraph in its CLAUDE.md explaining our sandbox. Every line a repo is asked to add is a reason not to adopt us. The rule governs a repository's contents, not a machine's; a machine enrolled to run agents was enrolled for exactly that.

So rules are filed by who they are true for. This file holds what is true of this project and of no other hosted repo, and only sessions working on this repo read it. `backend/sandbox/skills/cheese/SKILL.md` holds what is true of every repo the platform hosts, and every hosted repo's agents read it. A rule true only for platform-run agents working on this repo goes in the description of the platform tool it concerns, which is in front of exactly those agents. A platform rule written here fixes the problem for us alone; a project rule written in the skill tells every other repo something false about itself. When unsure: a rule that is false outside a session the platform runs is platform knowledge.

## Production changes go through CI/CD

dev and production are separate deployments; a change reaching one does not reach the other, and neither inherits the other's state. dev is deployed from main continuously, so a merge is a release: a live failure is fixed by merging the fix, and a careless merge reaches everyone just as fast. Treat the shared dev deployment as production.

- Code, deployment configuration and database schema reach a deployment only through the established pipeline: merge through the required checks, then deploy the resulting main commit or an approved release with pipeline-built or pinned images, through its release gates, deployment procedure and health verification.
- Passing CI on a feature branch does not authorise deploying that branch, including by triggering a workflow by hand.
- Do not substitute custom images, local builds or retagged candidates; hot-edit containers; mount candidate source over deployed code; run deployments or migrations by hand; or write an ad hoc script or workflow to get around the pipeline. Building an artifact in CI and handing it to someone to install is not the pipeline. Image recipes, dependency changes and recovery take the same path, recovery with database-compatible artifacts.
- SSH diagnosis is allowed. Pre-merge experiments belong in separate test environments, which this rule does not cover, and must not touch the production deployment or its data.
- Services maintained outside this repository (MicroCloud, the metering proxy) follow their own deployment and authorisation rules; do not impose this one on them. All other safety and authorisation rules still apply.

## The company's knowledge is in Feishu

What the product is for, who it serves, the partnerships behind it, facts about the company and the team's product decisions are kept in the team's Feishu wiki, not in this repository: the code shows what was built, not why or for whom. Before deciding anything a user or partner will see, such as product copy, scenarios or positioning, read the relevant Feishu pages through `lark-cli` and work from them, and name the pages you relied on in the PR; where you could not reach Feishu, say so in the PR instead of working from the code. Setting up access is in `docs/feishu-lark.md`.

## Read the issues before the docs, and clean the docs when a design lands

A design still being argued lives in issues. Whatever `docs/` says about it predates the conclusion, and nothing in the file will tell you.

When a design lands, delete every sentence it invalidated, in the same PR. A stale sentence reads exactly like a current one; a follow-up leaves the trap live until it happens, and an "outdated" note helps only whoever reads that far. Git keeps the history. Then close the issue with the reasoning, so the next agent finds a conclusion instead of re-arguing it.

## We are still building this: carry decisions out completely

Once something is decided, finish it. Leave no remnant of the old shape: no expiry note, compatibility path, deprecation marker, dual-write, flag keeping the old branch reachable, or doc section about the old way. Those protect existing users, which we do not have yet; each is more to unwind and a place the next reader mistakes for something in use.

Retiring something means deleting its code; adopting a replacement means deleting what it replaced. A new path can be contracted, documented and never reached while the old call site still serves every request, and both look done from the surface. The change is finished when the code that chooses changes, so check there, not in the menu, the contract or the doc.

Judge a mechanism by what it does now, not by why it was created. A check written after an incident points at where that incident surfaced; the code that causes it moves on.

## One table holds one kind of thing

A new kind of data does not go into an existing table as one more type value or one more `meta` key. A column only some kind of row would ever fill says that kind needs a table of its own. Different things share interfaces — references, search, permissions, events — not tables.

## Assume other agents are working right now

Concurrently, in this repo, on adjacent files. Before starting a fix, look for an open PR already making it. Before handing work off, review every path your change touches; a cache directory in that list is a stop sign.

Never `git stash`. The stash is one stack shared by every worktree, and a pop in yours applies someone else's uncommitted work and drops it from under them, silently. Commit instead; `git reset --soft HEAD~1` gives the staged state back. With `autoStash` on, a plain `git pull` stashes too: `task setup` turns it off for this repository, and `task git:config` does it for a clone made before that.

## Test behaviour, never the implementation

Write functional tests. Do not read the source to work out what to assert: a test derived from the implementation passes by construction and proves nothing, including after the implementation breaks.

A test earns its place by guarding a rule someone could state before the code existed: a product, security or data rule that a person who has never read the implementation can judge right or wrong. That holds at every layer. A component test that cancelling a dialog leaves the operation unrun is worth keeping; one that the dialog renders a particular sentence, class or DOM shape is not. Put the test at the lowest layer where the rule is observable without faking most of the system, assert what a user or caller can observe, and make sure it fails when the rule breaks, and only then. A test that goes red when the copy changes, or stays green when the rule is broken, costs more than it protects.

## Report real bugs, not theoretical ones

When auditing, a finding needs a way to actually happen: a crash, corruption, a security hole, a wrong result. Style opinions and "this could in principle" are noise that buries the real ones.

## Name the feedback a fix closes

The commit that completes the fix for a report in the dev deployment's feedback center (okcheese.com) carries `Fixes-feedback: FB-<n>` as a line of its own in its commit message, flush left, several numbers separated by commas. The dev deploy reads that line from main to mark the report fixed and shipped, and notifies whoever filed it; only commit messages reach main, never the PR description. A commit message that merely shows the syntax indents it, or it fires too. A production report's number names a different report on dev and never goes in this line.

## Fix bugs freely; propose product and interaction changes first

A bug — the product failing to do what it already sets out to do, including a message that says something untrue — is fixed without asking. A change to what people see or do is proposed to whoever you are working for, and waits for their yes before it merges: a new, moved or removed control, a new element on screen, new behaviour behind an existing action. This holds when the change is the natural fix for a bug; ship the part that restores the intended behaviour and propose the rest.

## Legal texts are not the work

The work here is code. Do not read or edit the terms or the privacy policy (`backend/app/domain/legal/texts/`). Only a task that names one of these texts makes it yours, and "Fix bugs freely" does not reach them.

## Follow a pull request you enqueue until it is deployed

Whoever puts a pull request into the merge queue watches it until the dev deploy that contains it has finished: `.claude/scripts/pr-watch.sh --enqueue --deploy <PR>`, run from the session that owns the work rather than inside a subagent that may end first. A background loop that dies with the agent that started it leaves a green PR unenqueued for hours, and a PR the queue removed looks the same as one still waiting.
