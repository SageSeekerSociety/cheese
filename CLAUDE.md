# Project Conventions

**Every line here must be a decision you could not reverse-engineer from the code.** This file is loaded in full on every turn, so anything inferable, anything a linter already enforces, and anything a competent model already does buries the rules that matter. One test: *delete this line — would an agent now get it wrong?* If no, delete it.

| What | Where it lives | Why there |
|---|---|---|
| Constraints you must obey **while changing code** | **this file** | loaded every turn, obeyed without being looked up |
| A design still being argued | **issues** | they are our ADRs, and an open state says "not settled" by itself |
| Long explanations, and how things currently are | **`docs/`** | read on demand; index in [`docs/README.md`](docs/README.md) |
| Pitfalls that only matter in one area | **`.claude/rules/`** | auto-loads when you touch matching paths |
| Anything about the **platform** — the `cheese` CLI, sandbox limits, jj workspaces | **`backend/sandbox/skills/cheese/SKILL.md`** | product code, injected into every session. **No repo should ever change in order to be hosted** — see below |

Monorepo: `backend/` (Python/FastAPI) + `frontend/` (Vue 3) + `cli/` (Go) + `e2e/` (Playwright) + `evals/`. `ls` at the root is the authoritative list. Any bundled CLI (`cheese`, `task`, `.claude/scripts/*.sh`) describes itself with `--help`; never restate a flag list in prose.

## This repo does not adapt to the platform

Cheese hosts other people's repositories, and **a repository must never have to change in order to be hosted** — no config file, no wrapper script, no paragraph in its CLAUDE.md explaining our sandbox. Every line a repo is asked to add is a reason not to adopt us, and for repos we do not control, asking is not on the table.

So this file describes **this project** and nothing else. Anything equally true of any hosted repo goes in the skill above, which reaches all of them at once; the same words here fix it for us alone and demonstrate the adaptation we promise nobody has to make. `.claude/scripts/check-repo-rules.sh` guards this, because the leak is invisible from inside — we are both the platform and a repo it hosts.

- *Would this still be true if cheese were hosting a stranger's Django app?* Then it belongs in the skill.
- **A rule here that is false when you are NOT in a sandbox is leaked platform knowledge.** One opening "there is no `.git` in a workspace" — plainly wrong on any laptop — sat at the top of this file for months.

## Before you fix anything: read the issues

**The current design lives in issues, not in `docs/`.** The machine shapes (#358), the compute model (#282, #442), the delivery transport (#480, #487) — each was last argued in a thread, and any doc mentioning it predates that argument. Search the open ones for the subsystem you are about to touch *before* you plan the fix.

**Shipping a design obliges you to clean `docs/` in the same PR.** A sentence describing the old design reads as current, so the next reader lands on a confident wrong conclusion. A follow-up is a promise while the trap is live; annotating it "outdated" leaves the trap and only helps whoever reads that far. **Delete the claim** — git history keeps it — then close the issue with the reasoning.

**`docs/` is strictly engineering documentation.** Product proposals, marketing copy, operational plans, competition material: never committed. Stage in `tmp/` (gitignored), upload to the Feishu wiki.

## Development Commands

```bash
task check                # everything (backend + frontend)
task be:check             # backend only
task be:test              # incremental tests
task be:db:migrate        # alembic upgrade head
```

After `git pull`: `bash .claude/scripts/post-pull.sh`. Backend runs via `uv run` from `backend/`; PG + Valkey in Docker. No docker? [`docs/testing-without-docker.md`](docs/testing-without-docker.md).

## Python

- Python >=3.13. `list[str]`, `X | None` natively. No `from __future__ import annotations`.
- **Never name a method `list`/`set`/`dict`/`type`** — they shadow builtins.
- Every signature annotated. No `Any` except at external boundaries.
- Pydantic v2 for schemas. Async throughout, `AsyncSession` for DB. DI via `Depends()`.

## Architecture

```
Route → Service → Repository → Model
backend/app/api/routes/ → domain/**/services.py → domain/**/repositories.py → domain/**/models.py
```

- **Routes**: parse, inject, call, return. No business logic.
- **Services**: all business logic, validation, cross-domain coordination.
- **Repositories**: data access only.
- **Models**: SQLAlchemy 2.0 (`mapped_column`, `Mapped[]`).

## API

- **A route's path is not a URL you can send.** The backend mounts at `/api` and the gateway strips one segment, so a 1.0 route is reached at `/api/users/…` and a 2.0 route at `/api/api/topics/…`. The doubling is load-bearing — flattening it makes six endpoints answer from the wrong generation. [`docs/api-conventions.md`](docs/api-conventions.md).
- REST on `/resource`. Pagination `pageStart` + `pageSize` → `{data: [...], total: int}`.
- Responses `{"code": 200, "message": "...", "data": {...}}`.
- Errors: `app.core.errors` classes, **never raw `HTTPException`** in the domain layer.
- Auth: `Depends(require_auth_user)`, or `get_auth_user` when public-aware. JWT: always check the `type` claim.

## Datetime

Every DB column is `TIMESTAMPTZ`. **Always pass `datetime.now(UTC)`; never `.replace(tzinfo=None)`** — a naive datetime does not raise, it silently reads as UTC and shifts the value.

## Security

Validate input via Pydantic. ORM only; raw SQL must use `text()` with bind params. No hardcoded secrets — `app.core.config.settings`.

## Testing

- Write **functional tests** — actual behavior, not source inspection.
- `pytest.mark.anyio` for async. `SimpleNamespace` + `AsyncMock`/`MagicMock` for fakes.
- `backend/tests/unit/` (no DB), `integration/` (DB-backed), `contract/` (API contract).
- **Tests must pass before any commit.** The pre-commit hook only exists once installed (`task hooks`); a VCS that skips git hooks bypasses it, and then CI is the only gate.
- New features require tests.

## Linting

**ruff** and **pyright**: zero errors. Frontend `pnpm run lint` (zero errors; existing warnings do not block) and `pnpm run typecheck` (`vue-tsc` behind `frontend/tsc-baseline.json`, which only ever ratchets down — fixed some? `typecheck:update` and commit it).

Rules a linter cannot express live in `.claude/scripts/check-repo-rules.sh` and `check-migration-fork.py`. Both run in `task check` and CI; both carry `--self-test`.

## Working in this repo

- Communicate in Chinese. Everything externally visible — code comments, docs, commits, PRs — in English.
- Bug auditing: real runtime bugs only (crashes, corruption, security, wrong behavior). Not style, not theoretical.
- **All commits go through PR.** Never commit directly to main.
- **Multiple agents work here concurrently.** Check open PRs and recent main commits for the same problem before starting. Before handing work off, review **every path your change touches** — a cache directory in that list (43k files once) is a stop sign.
- **Green that ran against an older main proves nothing about merging today.** Before merging a PR that adds a migration or touches symbols another open PR touches: `gh pr update-branch`, wait for the fresh run, then merge. GitHub re-evaluates conflicts continuously but never re-runs your checks — four individually-green PRs once merged into a three-headed alembic chain that 500'd the topic list.
- **Never `git stash` in a worktree.** Worktrees share one stash stack, so a `pop` returns whoever pushed last. Two sessions once ate each other's diffs this way. Write a patch instead.
- Box operations go through `deploy/deploy-docker.sh` only — runbook in `docs/infrastructure.md`. Hand-rolled `docker compose up` drops the image-pin exports and has broken dev.
- `reference/` (gitignored) holds the original NestJS and Kotlin implementations — consult when expected behavior is unclear.

## Commits

History is read by people who were never in the room: **English, Conventional Commits, no in-house jargon, no filler.** The machine-checkable half (type prefix, length, no trailing period, no CJK) is enforced by `domain/review/commit_message.py`. The half no parser sees:

- Subject: imperative, ≤72 chars, says what the change *does* — not which files moved. Needs an "and"? It is two commits.
- Body only when the subject cannot stand alone, and then it explains **why**: root cause, why this fix and not another. Never a list of what you did, never "all tests pass", never how to build the project, never differences between drafts of your own patch.
- Every commit atomic and buildable. Amend rather than stacking a "fix the last one" commit.

Attribution is automatic, via the topic owner's GitHub no-reply address — a PR showing up as the bot's means that account is not connected.

## Feishu

Product-direction docs are canonical in the **team Feishu wiki**; no local copies (a copy goes stale with nobody noticing). Real-name application material lives only in a restricted Feishu Drive folder — **never in git**. All lark-cli operations pass `--profile cheese`; `no_token`/`403`/`no authority` almost always means the wrong profile. [`docs/feishu-lark.md`](docs/feishu-lark.md).
