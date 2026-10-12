# Project Conventions

Principles that no tool will enforce, so they hold by being read. Two limits keep this file from bloating: a rule a linter or test already enforces has a home there, not here; and anything equally true of every repo the platform hosts belongs on the platform side (next section). A pitfall that only bites while touching one area goes in `.claude/rules/`; how things currently are, and the long explanations, go in `docs/`. Write what to do — a sentence about how things currently are turns false the moment someone fixes what it describes.

## This repo does not adapt to the platform

Cheese hosts other people's repositories, and a hosted repository must never have to change to be hosted: no config file, no wrapper script, no paragraph in its CLAUDE.md explaining our sandbox. The rule governs a repository's contents, not a machine's: a machine enrolled to run agents was enrolled for exactly that. When you build a platform feature here, that is the constraint it must meet.

So a rule is filed by who it is true for. A rule true of every hosted repo goes to the platform side — per-turn rules in the system prompt's platform rules (`backend/app/domain/agent/harness/prompt.py`), on-demand procedures in `backend/sandbox/skills/cheese/SKILL.md`, a rule true only for platform-run agents on this repo in the description of the platform tool it concerns — not into this file. When unsure: a rule that is false outside a session the platform runs is platform knowledge.

## Production changes go through CI/CD

dev and production are separate deployments that inherit nothing from each other; dev deploys from main continuously, so a merge is a release, and the shared dev deployment is treated as production. A change reaches a deployment only through the pipeline: merge through the required checks, then deploy the resulting main commit or an approved release with pipeline-built or pinned images through its release gates. Do not hot-edit a container, mount candidate source over deployed code, substitute a local build or retagged image, run a deployment or migration by hand, or write a script or workflow — including one that lands on the self-hosted box runner — to get around the pipeline; building an artifact in CI for someone to install by hand is not the pipeline, and a green feature branch does not authorise deploying it; image recipes, dependency changes and recovery take the same path, recovery with database-compatible artifacts. SSH diagnosis is allowed; pre-merge experiments go in a separate test environment this rule does not cover, and must not touch the production deployment or its data. A request to change dev does not authorise changing production or their shared compose files and deploy scripts: keep the change on the dev-only path unless the request names those shared files. MicroCloud and the metering proxy follow their own rules; the rest of this still applies to them.

## The company's knowledge is in Feishu

What the product is for, who it serves and the team's product decisions live in the Feishu wiki, not the code. Before deciding anything a user or partner will see — copy, scenarios, positioning — read the relevant pages through `lark-cli` and name them in the PR; where you could not reach Feishu, say so in the PR instead of working from the code. Setup: `docs/feishu-lark.md`.

## Read the issues before the docs

A design still being argued lives in issues; whatever `docs/` says about it predates the conclusion, and nothing in the file tells you. When a design lands, delete every sentence it invalidated in the same PR — a stale sentence reads exactly like a current one — and close the issue with the reasoning.

## We are still building this

Finish a decided change completely: leave no expiry note, compatibility path, deprecation marker, dual-write, flag keeping the old branch reachable, or doc section about the old way. Those protect existing users, which we have none of; each is more to unwind and a place the next reader mistakes for something in use. Adopting a replacement means deleting what it replaced, and the change is finished only when the code that chooses changes — check there, not in the menu, the contract or the doc.

Judge a mechanism by what it does now, not by why it was created. A check written after an incident points at where that incident surfaced; the code that causes it moves on.

## Prefer a mature library to a hand-rolled copy

For a problem the ecosystem already solves, prefer a library that is mature and that agents already know to an implementation written here. Before adding one, check whether something the project already depends on will do the job. Pin what you add, and change a lockfile only through its package manager (`uv`, `pnpm`), never by hand.

## One table holds one kind of thing

A new kind of data does not go into an existing table as one more type value or one more `meta` key; a column only one kind of row would ever fill means that kind needs a table of its own. Different things share interfaces — references, search, permissions, events — not tables.

## Assume other agents are working right now

Concurrently, in this repo, on adjacent files. Look for an open PR already making your fix before you start, and review every path your change touches before handing it off; a cache directory in that list is a stop sign. Never `git stash` — the stack is shared by every worktree, and a pop in yours applies someone else's uncommitted work and drops it from under them, silently; commit instead, and `git reset --soft HEAD~1` gives the staged state back. `task setup` turns `autoStash` off for this repository, so a plain `git pull` does not stash behind your back (`task git:config` does it for an older clone).

## Test behaviour, never the implementation

Write functional tests. Do not read the source to decide what to assert: a test derived from the implementation passes by construction and proves nothing, including after the implementation breaks. A test earns its place by guarding a rule someone could state before the code existed — a product, security or data rule that a person who has never read the implementation can judge right or wrong. That holds at every layer: a component test that cancelling a dialog leaves the operation unrun is worth keeping; one that the dialog renders a particular sentence, class or DOM shape is not. Assert what a user or caller can observe, at the lowest layer where the rule is observable without faking most of the system — a test that goes red when the copy changes, or stays green when the rule breaks, costs more than it protects.

## Run `task ci:fast` before you push

What the tier is, what it selects and its budget: `.github/scripts/ci-fast.md`. Run it before you push. Its frontend type check covers only the files you changed, so do not run the whole-tree `pnpm run typecheck` or `vue-tsc` in a sandbox: it can be killed for memory.

Exit 0 means the selected checks ran and passed, nothing more: an `unknown` or `blocked` result, and any check reported as not run, is not green — the tier ran out of budget, a tool was missing, or a report could not be judged, and none of that may stand in for a pass. Required CI and the merge queue remain the only merge gate.

## Report real bugs, not theoretical ones

An audit finding needs a way to actually happen — a crash, corruption, a security hole, a wrong result; style opinions and "this could in principle" are noise that buries the real ones.

## Name the feedback a fix closes

A commit that completes a fix for a report in the dev feedback center (okcheese.com) carries `Fixes-feedback: FB-<n>` as a line of its own, flush left, in its commit message — several numbers comma-separated. Only commit messages reach main, never the PR description; a production report's number names a different report and never goes in this line.

## Fix bugs freely; propose product and interaction changes first

A bug — the product failing to do what it already sets out to do, including a message that says something untrue — is fixed without asking. Anything a user sees or does is proposed and waits for a yes: a new, moved or removed control, a new element on screen, new behaviour behind an existing action. This holds when the change is the natural fix for a bug — ship the part that restores the intended behaviour and propose the rest.

## Legal texts are not the work

The work here is code. Do not read or edit the terms or privacy policy (`backend/app/domain/legal/texts/`) unless a task names one of those texts; "fix bugs freely" does not reach them.

## Follow a pull request you enqueue until it is deployed

Watch a pull request you put into the merge queue until the dev deploy that contains it has finished: `.claude/scripts/pr-watch.sh --enqueue --deploy <PR>`, run from the session that owns the work, not a subagent that may end first — a loop that dies with its agent leaves a green PR unenqueued for hours.
