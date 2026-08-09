# Project Conventions

Monorepo: `backend/` (Python/FastAPI) + `frontend/` (Vue 3) + `e2e/` (Playwright). See `README.md` for setup and commands.

Procedural guidance lives in `.claude/` (skills, path-scoped rules, agents, scripts — `ls .claude/` is the inventory), not in always-on prose here: review → `cheese-py-code-review` skill, post-pull → `post-pull` skill; area-specific pitfalls (migrations, backend tests, e2e) live in `.claude/rules/` and load automatically when you touch matching files. This file holds only the always-relevant conventions below.

## Development Commands

Backend runs locally via `uv run` from `backend/`. Infrastructure (PG, Valkey) in Docker. Use Taskfile:

```bash
task check                # all checks (backend + frontend)
task be:check             # backend only
task be:test              # incremental tests
task be:db:migrate        # alembic upgrade head
bash .claude/scripts/check.sh   # alternative: shell script
```

After `git pull`: `bash .claude/scripts/post-pull.sh`

## Python Conventions

- Python >=3.11. `list[str]`, `dict[int, str]`, `X | None` natively. No `from __future__ import annotations`.
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
- Tests MUST pass before any commit. Pre-commit hook enforces this.
- New features require tests (unit + integration as appropriate).

## Linting & Type Checking

- **ruff**: zero errors. **pyright**: zero errors in app code.
- Config in `backend/pyproject.toml` under `[tool.ruff]` and `[tool.pyright]`.

## Workflow Preferences

- Communicate in Chinese (user preference).
- Bug auditing: focus on real runtime bugs (crashes, data corruption, security, incorrect behavior). Do not report style issues or theoretical concerns.
- Commit messages in English, concise, focused on "why".
- After making changes, always run `task check` to verify.
- After `git pull`, run `bash .claude/scripts/post-pull.sh`.
- **All commits go through PR**: never commit directly to main.
- **Multiple agents work this repo concurrently.** Before starting a fix, check open PRs and recent main commits for the same problem; before `git add`/`commit` in a shared checkout, check for another session's activity (or use a separate worktree). Never `git add -A` — review the staged list; a cache directory in it (43k files once) is a stop sign.
- Box operations (env changes, container recreation) go through `deploy/deploy-docker.sh` only — see the runbook in `docs/infrastructure.md`. Hand-rolled `docker compose up` drops the deploy script's image-pin exports and has broken dev before.

## Documentation Map

Engineering docs live in `docs/` (git). **`docs/` is strictly for engineering documentation.** Non-engineering content (product proposals, marketing copy, operational plans, competition materials) must never be committed — stage in `tmp/` (gitignored) if Feishu is unavailable, then upload to the wiki when access is restored. Product-direction docs (vision, feedback) are canonical in the **team Feishu wiki** — no local copies are kept (staleness risk) — links in `docs/README.md`, fetch on demand via the lark skills. Real-name application materials live only in a restricted Feishu Drive folder, never in git.

To find or edit Feishu content, use the lark skills (`.claude/skills/lark-*`) and search at need (`lark-cli docs +search --profile cheese`) — do not maintain a static doc inventory here.

**lark-cli profile**: the team shares one Feishu org; all lark-cli Feishu operations must pass `--profile cheese` (app `cli_a97ca79454785bd5`, the only profile with drive/docs scopes). `no_token`/`403`/`no authority` almost always means a wrong profile, not a permission or cross-tenant problem. Setup, credential sync, and a troubleshooting table live in [`docs/feishu-lark.md`](docs/feishu-lark.md).
