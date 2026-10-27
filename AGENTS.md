# Project Conventions

Principles that no tool will enforce, so they hold by being read. Two limits keep this file from bloating: a rule a linter or test already enforces has a home there, not here; and anything equally true of every repo the platform hosts belongs on the platform side (next section). A pitfall that only bites while touching one area goes in `.claude/rules/`; how things currently are, and the long explanations, go in `docs/`. Write what to do — a sentence about how things currently are turns false the moment someone fixes what it describes.

## This repo does not adapt to the platform

Cheese hosts other people's repositories, and a hosted repository must never have to change to be hosted: no config file, no wrapper script, no paragraph in its CLAUDE.md explaining our sandbox. The rule governs a repository's contents, not a machine's: a machine enrolled to run agents was enrolled for exactly that. When you build a platform feature here, that is the constraint it must meet.

So a rule is filed by who it is true for. A rule true of every hosted repo goes to the platform side — per-turn rules in the system prompt's platform rules (`backend/app/domain/agent/harness/prompt.py`), on-demand procedures in `backend/sandbox/skills/cheese/SKILL.md` — not into this file. A rule true only of this repo goes here, even when only sessions the platform runs ever meet it: a platform tool's description and the platform's prompts are read by the agents of every hosted repo. When unsure: a rule that is false outside a session the platform runs, and true of every hosted repo, is platform knowledge.

## Production changes go through CI/CD

dev and production are separate deployments that inherit nothing from each other; dev deploys from main continuously, so a merge is a release, and the shared dev deployment is treated as production. A change reaches a deployment only through the pipeline: merge through the required checks, then deploy the resulting main commit or an approved release with pipeline-built or pinned images through its release gates. Do not hot-edit a container, mount candidate source over deployed code, substitute a local build or retagged image, run a deployment or migration by hand, or write a script or workflow — including one that lands on the self-hosted box runner — to get around the pipeline; building an artifact in CI for someone to install by hand is not the pipeline, and a green feature branch does not authorise deploying it; image recipes, dependency changes and recovery take the same path, recovery with database-compatible artifacts. SSH diagnosis is allowed; pre-merge experiments go in a separate test environment this rule does not cover, and must not touch the production deployment or its data. A request to change dev does not authorise changing production or their shared compose files and deploy scripts: keep the change on the dev-only path unless the request names those shared files. MicroCloud and the metering proxy follow their own rules; the rest of this still applies to them.

## The company's knowledge is in Feishu

What the product is for, who it serves and the team's product decisions live in the Feishu wiki, not the code. Before deciding anything a user or partner will see — copy, scenarios, positioning — read the relevant pages through `lark-cli` and name them in the PR; where you could not reach Feishu, say so in the PR instead of working from the code. The team's norms for product and engineering decisions are in 《产品与工程决策的方法》 (https://acnxgqu0961c.feishu.cn/wiki/Ah2Yw7eF8itAlrkoynpcdhisnIC) and 《产品思维：每个人的职责》 (https://acnxgqu0961c.feishu.cn/wiki/Jb1MwOwxyi6cMIkiKMWcFJ65nUe). Setup: `docs/feishu-lark.md`.

## Read the issues before the docs

A design still being argued lives in issues; whatever `docs/` says about it predates the conclusion, and nothing in the file tells you. When a design lands, delete every sentence it invalidated in the same PR — a stale sentence reads exactly like a current one — and close the issue with the reasoning.

## We are still building this

Build every decision for the end state: nothing here is yet too expensive to replace, so a decision made now is one we live with. Before changing anything, find the root cause from first principles and how mature systems solve the same problem, and build that design in its smallest complete form. A change that works around the cause instead of removing it — a special case, a retry or probe around a failure, a sweep that cleans up after something the design should prevent, a step a later stage would discard — is not merged, and one already on main is reverted. Where the right design is not settled, leave the problem open in an issue with what is known rather than spend work on a stopgap.

Finish a decided change completely: no expiry note, compatibility path, deprecation marker, dual-write, flag keeping the old branch reachable, or doc section about the old way, unless keeping one is itself the decision. People use the platform now, so a compatibility path may stay when the pull request states it as a design decision: whose data or workflow it protects, what carrying it costs, and the change that removes it. While the platform is young, a path that brings more debt than the disruption it spares is not kept; the data is migrated and the people affected are told instead. Every kept path is more to unwind and a place the next reader mistakes for something in use. Adopting a replacement means deleting what it replaced, and the change is finished only when the code that chooses changes — check there, not in the menu, the contract or the doc.

A change a page built against the old API cannot talk to — an operation removed; a request field removed, renamed, narrowed or newly required; a response field removed, renamed, or now possibly missing or of a type it never had; an enum value removed — raises the number in `backend/api-generation` in the same pull request, and the `api` job of Required CI fails one that does not. The backend then refuses pages still open on the old frontend and they ask to be refreshed, so changing both halves together needs no compatibility path for those pages.

Judge a mechanism by what it does now, not by why it was created. A check written after an incident points at where that incident surfaced; the code that causes it moves on.

## Prefer a mature library to a hand-rolled copy

For a problem the ecosystem already solves, prefer a library that is mature and that agents already know to an implementation written here. Before adding one, check whether something the project already depends on will do the job. Pin what you add, and change a lockfile only through its package manager (`uv`, `pnpm`), never by hand.

## One table holds one kind of thing

A new kind of data does not go into an existing table as one more type value or one more `meta` key; a column only one kind of row would ever fill means that kind needs a table of its own. Different things share interfaces — references, search, permissions, events — not tables.

## Assume other agents are working right now

Concurrently, in this repo, on adjacent files. Look for an open PR already making your fix before you start, and review every path your change touches before handing it off; a cache directory in that list is a stop sign. An independent review of a PR starts with `scripts/check.sh review <PR>` (the prepare step of `.claude/scripts/pr-review.sh`) and follows the REVIEW.md it prints, one method for every seat; do not assemble your own. The reading itself is one open-code-review run, started from the method's step 2 with `.claude/scripts/ocr-review.sh`; you judge what it reports, run the commands that settle a candidate, and write the result. OCR is a CLI, not a platform service, and this repo's task checkouts are partial clones — run it at the checked-out head, where the script can see the files, and it stops with a message when pointed anywhere else. It does not read tests or docs by default, so those files are uncovered rather than fine, and a run cut at its wall clock is a partial ledger: report its coverage numbers as they are instead of turning them into a verdict. The wall clock is the script's own `OCR_WALL_CLOCK` (28 minutes), closed with SIGTERM so OCR writes what it has; OCR's `--timeout` flag is not that ceiling, it is per file group and multiplied by the effort's round count. A run that exits non-zero, or whose every selected item failed, did not review anything — say so and hand the card in with no conclusion of your own. `prepare` also decides the change's review tier (A–G) by the files it touches, takes the highest, and prints what that tier means; the tier is part of the conclusion line. Tier G is a change that is only comments, formatting, a lock file or a clean revert: nobody reviews it, CI passing is enough, and the line `prepare` writes says so in the body, so 无 blocker/major there never reads as a judgement somebody made by reading. Its result goes into the card body, not onto GitHub: review the whole PR once; if that finds blockers or majors, fix them and review only the increment (`prepare <PR> --since <sha>`); an increment that only merges main or only changes tests or copy needs no review. Before handing off, `.claude/scripts/pr-review.sh ready <PR>` prints 可递 or what is missing — CI not green on the head, no conclusion line in the card body, a blocker or major, or an increment that needed a review and did not get one. Never `git stash` — the stack is shared by every worktree, and a pop in yours applies someone else's uncommitted work and drops it from under them, silently; commit instead, and `git reset --soft HEAD~1` gives the staged state back. `task setup` turns `autoStash` off for this repository, so a plain `git pull` does not stash behind your back (`task git:config` does it for an older clone).

## Test behaviour, never the implementation

A test guards a rule someone could state before the code existed — a product, security or data rule that a person who has never read the implementation can judge right or wrong — and checks it through what a user or caller can observe, at the lowest layer where the rule is observable without faking most of the system. Ask two questions of every test before you keep it: if the code stopped keeping its promise, would this go red? If the code were rewritten to keep the same promise another way, would it stay green? Both must be yes — behaviour-sensitive and structure-insensitive. A test that fails the second is a change detector: it reddens on every refactor, charges every later change an edit, and protects nothing. A component test that cancelling a dialog leaves the operation unrun passes both; one that the dialog renders a particular sentence, class or DOM shape fails the second.

So none of these is a test here, however its docstring argues: a scan of the source tree for "defined once", "stays deleted" or "called N times"; an assertion on what a workflow, compose file, Dockerfile or deploy script says; a run against fakes whose assertion is the exact command line or call the code made; a mock's `assert_called_with` standing in for an outcome; a CSS class, DOM shape or element count. A structural rule worth holding — an import boundary, a banned API — belongs to the linter built for it (`backend/.importlinter`, ruff, eslint). Ruff and eslint refuse source reads in tests; their allowlists only shrink, and nothing is added to them.

Not every change gets a test. A refactor, rename, deduplication or deletion, and a change to CI, configuration or a deploy script, leaves nothing a caller can observe: it is finished when the old code is gone and CI is green. A fix whose rule a caller can observe gets a regression test.

A test written to guard a fix has to be observed failing without that fix, and you are the one who observes it: run it against the code as it was and see the assertion go red, then against your change and see it go green. A regression test that was never red is the one case no reader can tell apart from a real one — it passes whether the bug is there or not — and the PR body's claim is the only place that pair of runs would ever be recorded. `.claude/scripts/prove-regression.py <node id> --without <revision>` runs both halves for you; it knows the pytest and vitest layouts and refuses a third rather than guess a runner, so pass it `--command`. It is a convenience, not a gate, and nothing checks whether you used it.

An existing test that goes red on your change is one of two things. If the behaviour changed on purpose, change the assertion to the new behaviour. If it did not, the test is a change detector: rewrite it to assert the outcome, or ask the person who gave you the task whether to delete it — name the test and the question it fails — and delete it only on their yes. Do not update its expected strings to match your change.

## A task can start before its machine exists

Nothing you run reaches the machine until it does, so wait for it once and only then set up the task's working tree: setup issued while the machine is still provisioning waits out the same interval and changes nothing, so retrying it buys a second wait rather than a working machine.

## Session paths do not exist on the work machine

The shell is on the work machine; the session's own files — `~/.cheese/memory/`, and everything under `/home/nictheboy/` — are not, and the machine answers `No such file or directory` for them. Read and write those with the Read, Write and Edit tools; `cat`, `tail`, `grep` and `ls` in Bash will not find them. The output path a backgrounded command prints in its receipt is one of those session paths, so a `tail` of it fails the same way: to see a long command's output on the machine, redirect it to a file you name there (`/tmp/<name>.log`), which both sides can see.

## A compacted session says where it is

Coming back from a context compaction, make your next tool call in that same turn, or send one line saying where the work stands and what comes next. Everyone watching the task — the person who asked for it, the seat waiting on it — reads a silent session exactly as a dead one, and pays for the silence twice: with the wait, and with whatever it runs to find out.

## The pull request's CI is the verdict

A ready pull request runs every suite its diff selects except the post-merge ones (e2e, remote execution, the MCP suites), with the backend's integration layer narrowed to the test files the diff reaches; a draft runs none, and marking it ready starts that run. `CI required` green on the head is the verdict a hand-off rests on. What runs where: `docs/manual/dev/ci.md`.

Locally, run the test files your change touches, to find a failure faster than a CI round would — not to stand in for one.

**Never run a whole-tree frontend type check in a sandbox — `pnpm run typecheck`, that same script invoked by hand (`node scripts/typecheck.mjs` from `frontend/`), or `vue-tsc`: the sandbox's memory cap kills it.** The pull request's CI type-checks the whole tree.

**The same goes for the whole backend unit suite.** A sandbox is missing what parts of it need, so a whole `backend/tests/unit` run reddens on the environment rather than on your change, and reading those failures one at a time costs more than the suite does. The full suite belongs to CI.

## Use what you changed before calling it done

CI checks what the tests encode; nobody has yet watched the change work. Once a change is deployed — on dev after the deploy that contains it, or on a test environment before merge — it is used by hand twice, each time doing what a user would do to reach the changed behaviour, not only loading the page: first by whoever made it (an agent drives a browser), then by a person, the one who asked for it or the one who merged it. An agent's hand-off names the steps that person is to try. Both record what they did and saw under `Hands-on check` in the pull request body; a change no user can reach (CI, docs, a refactor with no visible effect) says so there instead.

A change to anything a user sees — a screen, a control, its copy — also comes with an explanation of the affected screens before and after, in a form that fits the change and in terms a user would understand, handed with the pull request link to whoever asked for the change. A frontend change with no visible effect needs none.

## Report real bugs, not theoretical ones

An audit finding needs a way to actually happen — a crash, corruption, a security hole, a wrong result; style opinions and "this could in principle" are noise that buries the real ones.

## Name the feedback a fix closes

A commit that completes a fix for a report in the dev feedback center (okcheese.com) carries `Fixes-feedback: FB-<n>` as a line of its own, flush left, in its commit message — several numbers comma-separated. Only commit messages reach main, never the PR description; a production report's number names a different report and never goes in this line.

## A task's last merge posts its record

When a task in this repository closes after its last delivery merged, post, before closing it, one message of its own whose first line is 【留痕记录】, then: the task name, the PR number and link, its title, its merge time in the reader's local time, and 正文： followed by the full PR body verbatim. The team copies it whole into its work-record sheet, so it repeats the PR body on purpose and is neither folded into the write-up nor split.

## Fix bugs freely; propose product and interaction changes first

A bug — the product failing to do what it already sets out to do, including a message that says something untrue — is fixed without asking. Anything a user sees or does is proposed and waits for a yes: a new, moved or removed control, a new element on screen, new behaviour behind an existing action. This holds when the change is the natural fix for a bug — ship the part that restores the intended behaviour and propose the rest.

## Legal texts are not the work

The work here is code. Do not read or edit the terms or privacy policy (`backend/app/domain/legal/texts/`) unless a task names one of those texts; "fix bugs freely" does not reach them.

## Read a pull request's mergeability before you wait on it

Before waiting on CI, and again before handing the card in, ask the forge whether the pull request still merges: `gh pr view <PR> --json mergeable,mergeStateStatus`, where `CONFLICTING` or `DIRTY` means it does not. A conflicting pull request cannot merge however green its own checks are, so settle it first: on the task's branch, `git merge --no-autostash origin/main`, resolve, run the checks the change touches, and push. Merge, never rebase: the branch has more than one writer, and the forge replaces a rewritten tip only for the checkout that rewrote it, while a merge is a commit every writer can push. The `--no-autostash` is not optional, for the reason above: the stash stack is shared by every worktree, and an automatic stash takes someone else's work with it.

## Watch a pull request in the background, never by polling

**Do not poll in the foreground: neither `sleep N` nor a `for`/`while` loop around `ps` or `gh`. The command timeout cuts a foreground poll off (`Exit code 143`, two minutes on most commands), so it waits for nothing and then has to be repeated.** Watch a pull request with `.claude/scripts/pr-watch.sh --enqueue --deploy <PR>` from a `ready` result for the current head through squash merge, push-to-main Required CI and the dev deploy that contains it. The watcher re-reads the head before merging and pins the merge to it; it never arms auto-merge. Run it in the background with output redirected to a file, under a monitor from the session that owns the work, so a subagent ending cannot abandon the watch and every changed state reaches the session.
