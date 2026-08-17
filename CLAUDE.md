# Project Conventions

Monorepo: `backend/` (Python/FastAPI) + `frontend/` (Vue 3) + `cli/` (Go) + `e2e/` (Playwright) + `evals/`. See `README.md` for setup and commands. When you need the full list of language surfaces (CI coverage audits, lint/test sweeps), get it from `ls` at the repo root — not from this line.

Procedural guidance lives in `.claude/` (skills, path-scoped rules, agents, scripts — `ls .claude/` is the inventory), not in always-on prose here: review → `cheese-py-code-review` skill, post-pull → `post-pull` skill, running tests where there is no docker → `.claude/scripts/dev-db.sh` (see Testing); area-specific pitfalls (migrations, backend tests, e2e) live in `.claude/rules/` and load automatically when you touch matching files. This file holds only the always-relevant conventions below.

**Where a tool can describe itself, let it.** Run any bundled CLI (`cheese`,
`task`, `.claude/scripts/*.sh`) with `--help` to discover its flags, arguments
and usage — that layer is generated from the code, so it cannot go stale. Prose
here and in `.claude/` documents **only what `--help` cannot tell you**: why a
thing exists, the pitfalls, the output contract, and when *not* to reach for it.
Adding a flag should never oblige anyone to edit a markdown file; if you catch
yourself restating a flag list in prose, delete the prose instead.

## Before you fix anything: read the issues, then `docs/`

**The current design lives in issues, not in `docs/`.** The machine shapes
(#358), the compute model (#282, #442), the delivery transport (#480, #487) —
each was last argued in an issue thread, and any doc mentioning it was written
before that argument finished. So the order is **issues that own the area →
`docs/` → the code**. Starting from `docs/` means implementing a design that was
already superseded, and nothing in the file will tell you.

Issues are readable from a sandbox — `cheese gh-token`, see Sandbox Capability
Boundaries below. Search the open ones for the subsystem you are about to touch
*before* you plan the fix, not after.

**The division of labour** — issues carry what is still being decided plus the
argument for why it was decided that way; they *are* our ADRs, and an open state
is itself the statement "not settled". `docs/` carries only two things:
constraints you must obey while changing code, and how the thing currently
behaves. The test is **"if this paragraph goes stale, who notices?"** If the
answer is "nobody, until someone follows it into a mistake", it belongs in an
issue.

**Settling a design obliges you to clean `docs/` in the same PR.** The moment a
design ships, every sentence describing the old one becomes a trap: it reads as
current, so the next reader reaches a confident wrong conclusion with nothing to
signal the error. Fixing it in a follow-up is a promise, and the trap is live
meanwhile. Do not annotate it either ("this section is outdated") — that leaves
the trap in place and only helps whoever reads that far. **Delete the claim**;
git history keeps it. Then close the issue the PR settled, with the reasoning,
so the next agent finds a conclusion instead of re-litigating it.

**`docs/` is strictly engineering documentation.** Product proposals, marketing
copy, operational plans, competition materials: never committed — stage them in
`tmp/` (gitignored) and upload to the Feishu wiki.

## Version control is jj, not git

There is no `.git` in a workspace — only `.jj`. This is the one thing your
environment actively lies to you about, so it lives here rather than in a
path-scoped rule: the misleading signal reaches you on turn 1, before you have
touched any file that a rule could key off. The second reason is worse — you
find out you cannot see the remote only at the moment you first try to check it,
which is *after* a round of work, one sentence before you report it done. Path
triggering is too late for both.

| Symptom | What it looks like | Actual cause |
|---|---|---|
| `Is a git repository: false` in your environment block | "this checkout has no version control" | It has jj. Only the `.git` probe fails. |
| `git status` / `gh pr list` → `fatal: not a git repository` | broken checkout | Same. `gh` needs an explicit `-R <owner>/<repo>`; it cannot infer the remote without `.git`. |
| `jj git fetch` → `Git does not recognize required option: porcelain` | the fetch is broken, retry it | The sandbox ships git 2.39 and jj wants ≥ 2.41. **No `jj git` remote traffic works from inside the box** — the platform syncs `main@upstream` for you on the host. Read that ref, don't repair git. |
| `gh api repos/<o>/<r>/pulls/<n>` → 403, but `repos/<o>/<r>` → 200 | the token expired, or the PR doesn't exist | A permission the token doesn't carry. **`cheese gh-token` prints the list on stderr — read it instead of guessing.** It is not a fixed set: it's whatever the platform's GitHub App was granted, narrowed to read. |
| `jj rebase -d main@upstream` → `Commit ... is immutable` | you lack permission | You aimed at shared history. Rebase *your own* change only: `jj rebase -s <your-change-id> -d main@upstream`. |

**You cannot observe the remote, so never report on it.** Fetch fails and the
token cannot read a ref or a PR — no command in this box will tell you what a
branch tip actually is, and pushing happens on the platform's side, not yours.
Real incident (2026-08-11, PR #267): an agent reported "the change went to the PR
branch with the snapshot"; the tip had not moved and the conflict was still
there. It was not lying — it had no way to look. So anything about remote state
(pushed, merged, conflict resolved, CI green) is a **claim, not an observation**:
label it unverified and let a human confirm. What you *can* verify is local —
`jj log`, `jj status`, and `jj diff` against `main@upstream`.

Command mapping — `jj log`, `jj status`, `jj diff`, `jj file show -r <rev> <path>`,
`jj bookmark list`. Two habits do not carry over: there is **no staging area**
(the working copy already *is* a commit, so `git add` has no equivalent and
nothing needs committing by hand), and a fresh sandbox has **no jj identity**
configured, so anything you would push must go through the platform.

Your workspace can be **behind `main@upstream`** — sub-topic workspaces are cut
when the topic is split, and main moves. Before editing a file other agents also
touch, diff it against `main@upstream` and rebase; editing on a stale base is how
you silently revert someone's merged PR. To git that is an ordinary modification,
not a conflict, so it merges clean and no check catches it: **if you are editing
a file you did not create, say so in your report** and let a human look.

## Development Commands

Backend runs locally via `uv run` from `backend/`. Infrastructure (PG, Valkey) in Docker — where there is no docker (agent sandboxes), `.claude/scripts/dev-db.sh` stands the test servers up instead (see Testing). Use Taskfile:

```bash
task check                # all checks (backend + frontend)
task be:check             # backend only
task be:test              # incremental tests
task be:db:migrate        # alembic upgrade head
bash .claude/scripts/check.sh   # alternative: shell script
```

After `git pull`: `bash .claude/scripts/post-pull.sh`

## Sandbox Capability Boundaries

Every "can't" here was reproduced in an agent sandbox; none is hypothetical.

| Need | The supported path |
|---|---|
| Read anything on GitHub — CI logs, check runs, **issue bodies and comments**, PR review comments, files outside your workspace | `cheese gh-token` mints a ~1h read-only GitHub token → `GH_TOKEN=$(cheese gh-token) gh api ...`. **This is the only sanctioned route**, and nothing else in the repo will lead you to it. It prints on stderr exactly what this token may read, plus copy-paste commands for each — **read those lines before concluding you can't** |
| Run the DB-backed test suite without docker | `.claude/scripts/dev-db.sh` (see Testing) |
| Long-running commands | `cheese await` — the platform wakes the topic with the exit code, instead of you blocking |

That token is read-only and its scope is **not a constant**: it is whatever the
platform's GitHub App holds, narrowed to `read`, so a permission an admin has
not granted returns **403 Resource not accessible by integration**, while one
granted this morning starts working with no deploy. The `cheese gh-token` print
is the source of truth — not this file, not your memory of last week. What is
fixed is the ceiling: nothing a sandbox is handed can ever write. Hit a 403 on
something you genuinely need to read? **Say so in your report** — the fix is an
App permission an admin can add, not a workaround, and not a human pasting the
content in for you.

Everything else the sandbox cannot do — no remote traffic, no way to observe the
remote, checks that pass on a stale base — has one cause and is covered under
**Version control is jj, not git** above.

## Python Conventions

- Python >=3.13. `list[str]`, `dict[int, str]`, `X | None` natively. No `from __future__ import annotations`.
- Do NOT name methods `list`, `set`, `dict`, `type` — they shadow builtins.
- All function signatures must be type-annotated. No `Any` unless at external boundaries.
- Pydantic v2 for request/response schemas.
- Async/await throughout. `AsyncSession` for database access.
- Dependency injection via FastAPI `Depends()`.

## Architecture

```
Route → Service → Repository → Model
(backend/app/api/routes/) → (backend/app/domain/**/services.py) → (backend/app/domain/**/repositories.py) → (backend/app/domain/**/models.py)
```

- **Routes**: parameter parsing, DI, call service, return response. No business logic.
- **Services**: all business logic, validation, cross-domain coordination.
- **Repositories**: data access only. No business logic.
- **Models**: SQLAlchemy 2.0 style (`mapped_column`, `Mapped[]`).

## API Design

- **A route's path is not a URL you can send.** The backend is mounted at `/api` on the app origin, and the gateway strips that one segment — so a 1.0 route (`/users/…`) is reached at `/api/users/…` and a 2.0 route (which carries its own `/api`) at `/api/api/topics/…`. The doubling is deliberate and load-bearing; flattening it makes six endpoints answer from the wrong generation. Writing a client, or wondering why a call 404s or returns HTML: [`docs/api-conventions.md`](docs/api-conventions.md).
- RESTful: GET/POST/PUT/DELETE on `/resource`.
- Pagination: `pageStart` + `pageSize`. Return `{data: [...], total: int}`.
- Response format: `{"code": 200, "message": "...", "data": {...}}`.
- Errors: use `app.core.errors` classes, not raw `HTTPException`.
- Auth: `Depends(require_auth_user)` for protected endpoints; `Depends(get_auth_user)` for public-aware.
- JWT: `Authorization: Bearer <token>`, always check `type` claim.

## Datetime

- All DB columns use `DateTime(timezone=True)` (PostgreSQL `TIMESTAMPTZ`).
- Always pass `datetime.now(UTC)` (timezone-aware). Never use `.replace(tzinfo=None)`.

## Security

- Validate all input via Pydantic schemas.
- Use SQLAlchemy ORM for queries (parameterized). Raw SQL must use `text()` with bind params.
- No hardcoded secrets; use `app.core.config.settings`.

## Reference Code

The root-level `reference/` directory (gitignored) contains original implementations. Consult when unclear about expected behavior:

- `reference/cheese-backend/` — NestJS/TypeScript (comments, materials, answers, questions)
- `reference/cheese-backend-nt/` — Kotlin/Spring Boot (teams, tasks, spaces, notifications, auth, AI)

## Testing

- Write **functional tests** that test actual behavior. Do NOT inspect source code.
- `pytest.mark.anyio` for async tests. `SimpleNamespace` + `AsyncMock`/`MagicMock` for fakes.
- Locations: `backend/tests/unit/` (no DB), `backend/tests/integration/` (DB-backed), `backend/tests/contract/` (API contract).
- Tests MUST pass before any commit. A pre-commit hook enforces this **once you install it** — a fresh clone has no hook. Run `task hooks` (or `bash .claude/scripts/install-hooks.sh`). Committing through `jj` bypasses git hooks entirely; on that path CI is the only gate.
- New features require tests (unit + integration as appropriate).

### Running tests in a sandbox (no docker)

An agent sandbox has neither Postgres nor docker, but the suite still runs — the
DB-backed tests need a server, not docker. `.claude/scripts/dev-db.sh` starts
Postgres + Redis from prebuilt wheels (`pgserver`, `redislite`) fetched by `uv`:

```bash
eval "$(bash .claude/scripts/dev-db.sh start)"   # exports TEST_PG_BASE + REDIS_URL
cd backend && uv run pytest tests/ -n 4 -q
bash .claude/scripts/dev-db.sh stop --purge      # stop + delete the data dir
```

- **Redis is required, not optional** — 2FA/login/session state lives there, so
  the integration suite errors without it.
- Those two wheels are deliberately NOT backend dependencies: `pgserver` ships no
  cp313 wheel and this project is `requires-python >=3.13`, so declaring it would
  break resolution. The script pins them and runs them on their own throwaway
  3.12 interpreter; tests still run on 3.13.
- `tests/unit/` needs no server at all (`conftest.py` provisions the schema only
  for tests that ask for it). Everything outside `tests/unit/` is DB-backed.
- `check.sh --no-tests` (what the quality gate uses) skips pytest entirely — use
  the recipe above to actually exercise the suite.

**Set a git identity before you run the suite** — a fresh sandbox has none, and a
container rebuild wipes it again, so re-run this whenever the failures reappear:

```bash
git config --global user.email "you@example.com"
git config --global user.name  "Your Name"
```

Without it `git commit` refuses, and ~43 tests fail wherever one builds a real
worktree (`test_workspace.py`, `test_upstream.py`, `test_accept*.py`,
`test_git_http.py`, `test_attachments.py` and friends). `GIT_*` env vars do *not*
work here — conftest strips them on purpose (see the comment at the top of the
file) — but it never touches global config, which is why the `git config` route
does. Verified: with the identity set the whole batch goes green.

**Install `jj` too** — same story as the git identity: a container rebuild wipes
it, and nothing in `uv sync` puts it back. Symptom is unmistakable once you know
it: every DB-backed test that builds a real workspace dies with
`FileNotFoundError: [Errno 2] No such file or directory: 'jj'` (10 failures in
`test_upstream.py` alone). Project repos are jj-colocated, so the app shells out
to the binary. Install the same version CI does (`.github/workflows/test.yml`):

```bash
curl -sSL https://github.com/jj-vcs/jj/releases/download/v0.43.0/jj-v0.43.0-x86_64-unknown-linux-musl.tar.gz \
  | tar -xz -C /tmp/jj-dl && mv /tmp/jj-dl/jj ~/.local/bin/jj && jj --version
```

Three sandbox gaps are left, and all are **missing host setup, not code defects**.
Don't spend time re-diagnosing them:

- **No procps** (`ps`/`pgrep`/`kill` binaries absent; bash's `kill` is a builtin
  only) → failures in `test_machine_service.py`, `test_tmux_control.py` with
  `FileNotFoundError: 'kill'`.
- **No openssh-client** (`ssh-keygen` absent) → 21 failures in
  `test_machine_service.py` with `FileNotFoundError: 'ssh-keygen'`. Same file as
  the procps gap, so the two look like one 22-failure blob; they are not.
- **No provider credentials** → 1 failure,
  `test_market_api.py::test_market_lists_ai_and_compute_pools`. A profile's
  `available` is `bool(auth_token or oauth_token)` (`app/domain/agent/profiles.py`),
  so with `settings.anthropic_auth_token` unset the default AI pool honestly
  reports unavailable. Any non-empty value clears it —
  `ANTHROPIC_AUTH_TOKEN=dummy-for-test uv run pytest tests/integration/test_market_api.py`
  goes green; nothing calls the provider.

## Linting & Type Checking

- **ruff**: zero errors. **pyright**: zero errors in app code.
- Config in `backend/pyproject.toml` under `[tool.ruff]` and `[tool.pyright]`.
- **Frontend**: `pnpm run lint` (ESLint — zero errors; the 288 existing warnings do not block) and `pnpm run typecheck` (`vue-tsc` behind a ratchet: `frontend/tsc-baseline.json` freezes the pre-existing errors, any NEW one fails). Fixed some? `pnpm run typecheck:update` and commit the baseline — it only ever goes down. `pnpm run lint` never writes; use `lint:fix` for that.
- Rules in this file that a linter cannot express are enforced by `.claude/scripts/check-repo-rules.sh` (naive datetime, builtin-shadowing method names, raw `HTTPException` in the domain layer) and `.claude/scripts/check-migration-fork.py` (a merge that would fork the alembic chain). Both run in `task check` and in CI's Repo Guards workflow; both carry `--self-test`.

## Workflow Preferences

- Communicate in Chinese (user preference).
- Bug auditing: focus on real runtime bugs (crashes, data corruption, security, incorrect behavior). Do not report style issues or theoretical concerns.
- Commit messages, PR titles and PR bodies in English, Conventional Commits — see **Commits and PRs** below.
- After making changes, always run `task check` to verify.
- After `git pull`, run `bash .claude/scripts/post-pull.sh`.
- **All commits go through PR**: never commit directly to main.
- **Multiple agents work this repo concurrently.** Before starting a fix, check open PRs and recent main commits for the same problem. In a jj workspace there is nothing to stage — the working copy is the commit — so the equivalent discipline is to run `jj status` before you hand work off and review every path in it. A cache directory in that list (43k files once) is a stop sign.
- **Green that ran against an older main proves nothing about merging today.** Before merging any PR that adds a migration or touches symbols another open PR also touches: update the branch (`gh pr update-branch`), wait for the fresh run, then merge. GitHub re-evaluates CONFLICTING continuously but never re-runs your checks — on 2026-08-12 four individually-green PRs merged into a three-headed alembic chain and a duplicated function that 500'd the topic list. Migrations also move `backend/alembic/HEAD` (one line, see `.claude/rules/migrations.md`) precisely so concurrent migration PRs collide in git instead of merging cleanly into a fork.
- **Never `git stash` in a worktree.** Worktrees share one `.git`, so they share one stash stack: a `pop` returns whichever session pushed last, not yours. Two sessions collided this way on 2026-08-12 and each popped the other's diff — recoverable only via `git fsck --unreachable`, and the stack already held several `recovered:` entries from earlier collisions. To set changes aside, write a patch (`git diff > /tmp/x.patch`) or add another worktree.
- Box operations (env changes, container recreation) go through `deploy/deploy-docker.sh` only — see the runbook in `docs/infrastructure.md`. Hand-rolled `docker compose up` drops the deploy script's image-pin exports and has broken dev before.

## Commits and PRs

The repository's history is read by people who were never in the room. Write for
them: **English, Conventional Commits, no in-house jargon, no filler.**

### The one line that matters

On this platform a topic is squash-merged, so a whole topic becomes **one
commit**, and its subject is whatever you filed the accept card with:

```bash
cheese accept-request lisi "最懂这块" \
  --subject 'fix(accept): open the PR as the requester, not the bot' \
  --body 'PRs opened with the App token belong to the bot on GitHub, so the
person whose work it is gets no attribution and cannot filter for it.'
```

`--subject` is the PR title **and** the squash commit subject, and it is
**required** — the backend refuses a card without one, and refuses a malformed
one too (`domain/review/commit_message.py`), so you find out in the same breath,
not next month. It used to be optional, with `chore: <话题标题>` filled in when
it was missing; that is a room name in `git log`, which is the thing this
convention exists to stop.

### Subject

`type(scope): description` — types are `feat fix docs style refactor perf test
build ci chore revert`, `!` before the colon marks a breaking change.

- Imperative mood, ≤72 characters, no trailing period.
- Say what the change *does*, not which files moved. The diff already lists files.
- One commit, one thing. If the subject needs an "and", it is two commits.

### Body

Only when the subject is not self-explanatory. Then it explains **why** — the
root cause, why this is a bug, why this fix and not another. Never:

- a numbered list of what you did (that is the diff);
- "all tests pass", "build succeeds" (a commit that exists passed CI);
- how to build or run the project (that is documentation);
- differences between drafts of your own patch ("v2 fixes X") — describe the
  final state relative to the parent commit, nothing else.

Assume a competent reader who already knows this project. Non-obvious commands,
non-standard configuration needed to reproduce, and genuinely surprising
constraints DO belong; anything they could have guessed does not.

### What goes in a commit

- Atomic and buildable. Every commit compiles and passes its own tests.
- Never patch up the previous commit with a new one. In jj the working copy IS
  the commit, so amend by editing and re-describing (`jj describe -m …`) rather
  than stacking a "fix the last one" commit.
- Exploratory work stays on a scratch branch; clean it into atomic commits
  before it goes anywhere.

### Attribution

Commits and PRs are attributed to the human whose topic it is, via their GitHub
no-reply address (`backend/app/domain/workspace/identity.py`) — that requires
them to have connected GitHub in 设置. Nothing to do by hand; if a PR shows up
as the bot's, the account is not connected.

## Documentation Map

[`docs/README.md`](docs/README.md) is the index — grouped by what goes stale
first, with anything superseded by an issue or never implemented flagged as
such, so you know before opening it whether it still counts. What may live in
`docs/` at all is settled above, under **Before you fix anything**.

Product-direction docs (vision, feedback) are canonical in the **team Feishu
wiki**, and no local copy is kept — a copy goes stale without anyone noticing,
which is the failure this whole section exists to prevent. Links are in
`docs/README.md`; fetch on demand via the lark skills (`.claude/skills/lark-*`,
`lark-cli docs +search --profile cheese`) rather than maintaining a static
inventory here. Real-name application materials live only in a restricted Feishu
Drive folder, never in git.

**lark-cli profile**: the team shares one Feishu org; all lark-cli Feishu operations must pass `--profile cheese` (app `cli_a97ca79454785bd5`, the only profile with drive/docs scopes). `no_token`/`403`/`no authority` almost always means a wrong profile, not a permission or cross-tenant problem. Setup, credential sync, and a troubleshooting table live in [`docs/feishu-lark.md`](docs/feishu-lark.md).
