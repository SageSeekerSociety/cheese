# Project Conventions

## Monorepo Structure

```
cheese-backend-py/               # monorepo root
├── backend/                     # Python/FastAPI backend
│   ├── app/                     # source code
│   ├── tests/                   # unit + integration + contract tests
│   ├── migrations/              # alembic migrations
│   ├── pyproject.toml           # Python dependencies
│   ├── Taskfile.yml             # backend-specific tasks
│   └── Dockerfile               # production build
├── frontend/                    # Vue 3 / TypeScript frontend
│   ├── src/                     # source code
│   ├── package.json             # JS dependencies
│   ├── Taskfile.yml             # frontend-specific tasks
│   └── vite.config.ts
├── e2e/                         # Playwright E2E tests
│   ├── tests/                   # test specs
│   ├── playwright.config.ts
│   └── Taskfile.yml             # e2e-specific tasks
├── docker-compose.yml           # infrastructure (PG, Valkey, ES)
├── Taskfile.yml                 # root task runner (includes be: + fe: + e2e:)
├── CLAUDE.md                    # project conventions (this file)
├── .claude/                     # AI tooling
└── .github/workflows/           # CI (path-filtered per project)
```

Root Taskfile includes sub-Taskfiles with prefixes: `be:` (backend), `fe:` (frontend), `e2e:` (E2E tests).

## .claude/ Directory Structure

This directory contains AI-assisted development tooling. Other AIs (or humans) should read it to understand how this project is developed:

```
.claude/
├── agents/
│   └── check-runner.md              # Background test runner (parallel to review)
├── skills/
│   └── cheese-py-code-review.md     # Code review checklist and workflow
├── scripts/
│   ├── check.sh                     # ruff + pyright + pytest, one command
│   ├── post-pull.sh                 # Post-git-pull checks (migrations, deps, health)
│   └── pre-commit                   # Copy to .git/hooks/ to block commits on check failure
├── reference/
│   ├── post-pull-checks.md          # Detailed post-pull checklist
│   ├── parallel-review.md           # Review workflow (parallel + serial modes)
│   └── sync-rule.md                 # CLAUDE.md ↔ .claude/ sync rules (highest priority)
└── settings.json                    # Permissions allowlist for common commands
```

- **agents/** — Subagent definitions. `check-runner` runs tests in background while main review proceeds.
- **skills/** — AI skill definitions. `cheese-py-code-review.md` is the code review skill.
- **scripts/** — Shell scripts for common workflows. Use these to save tokens.
- **reference/** — Project-specific specs and checklists (tracked in git).
- **settings.json** — Pre-approved commands to reduce permission prompts.

## Operating System

This project targets **Linux / macOS**. Python runs **directly on the host** via `uv run` inside the `backend/` directory. Infrastructure services (PostgreSQL, Valkey, Elasticsearch) run in Docker:

```bash
docker compose up -d                          # start infrastructure (DB, Redis, ES)
docker compose ps                             # check infrastructure status
docker compose logs -f postgres               # view specific service logs
docker compose down                           # stop infrastructure
```

The backend runs locally:

```bash
cd backend
uv sync                                       # install dependencies (first time / after lock change)
uv run uvicorn app.main:app --port 8081 --reload  # start dev server
uv run pytest tests/ -n 8 --testmon -q        # run tests
```

Or use Taskfile (recommended):

```bash
task infra                                    # start infrastructure
task dev                                      # start backend (starts infra automatically)
task check                                    # ruff + pyright + pytest
task --list                                   # see all available tasks

# Backend tasks directly:
task be:test                                  # incremental tests
task be:lint                                  # ruff only
task be:db:migrate                            # alembic upgrade head
```

**Important**: Dockerfile is kept for **production builds only**. Do NOT use `docker compose exec` for development.

## Pre-written Scripts

Instead of typing long commands, use Taskfile or the scripts in `.claude/scripts/`:

```bash
task check                          # ruff + pyright + pytest (recommended)
task be:test                        # incremental tests only
task be:lint                        # ruff only
task be:db:migrate                  # alembic upgrade head

# Or use scripts directly:
bash .claude/scripts/check.sh       # ruff + pyright + pytest, prints "3/3 passed" or failures
bash .claude/scripts/post-pull.sh   # migration check, alembic upgrade, dep sync, health check
bash .claude/scripts/pre-commit     # install as .git/hooks/pre-commit to block commits on check failure
```

Root-level `Taskfile.yml` includes `backend/Taskfile.yml` with `be:` prefix. See `task --list` for all commands.

## Python Conventions

- Python >=3.11. Version pinned in `.python-version`, managed by uv.
- Do NOT use `from __future__ import annotations`. `list[str]`, `dict[int, str]`, `X | None` work natively.
- Do NOT name methods `list`, `set`, `dict`, `type` — they shadow builtins and break annotations.
- Use quoted forward references (`"ClassName"`) only for genuine self-referencing cases (e.g., `@staticmethod` returning the class itself).
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

The root-level `reference/` directory (gitignored) contains original implementations this project is migrated from. Consult when unclear about expected behavior:

- `reference/cheese-backend/` — NestJS/TypeScript (comments, materials, answers, questions)
- `reference/cheese-backend-nt/` — Kotlin/Spring Boot (teams, tasks, spaces, notifications, auth, AI)
- `reference/cheese-frontend/` — Vue (API contract expectations)

This is distinct from `.claude/reference/` which contains project development specs (tracked in git).

## Testing

- Write **functional tests** that test actual behavior. Use mocked dependencies, call the function, assert outputs. Do NOT inspect source code (`inspect.getsource` is unacceptable).
- `pytest.mark.anyio` for async tests.
- `SimpleNamespace` + `AsyncMock`/`MagicMock` for fakes.
- Test locations:
  - `backend/tests/unit/` — unit tests (no DB)
  - `backend/tests/integration/` — DB-backed tests
  - `backend/tests/contract/` — API contract comparisons

### Running Tests

```bash
# Full suite (parallel, ~25s locally):
task be:test:full
# Or: cd backend && uv run pytest tests/ -n 8 -q

# Incremental (only tests affected by your changes, ~5-10s):
task be:test
# Or: cd backend && uv run pytest tests/ -n 8 --testmon -q

# Only last-failed (TDD loop):
task be:test:failed
# Or: cd backend && uv run pytest tests/ --lf -n 8 -q

# Full check (ruff + pyright + pytest):
task check
# Or: bash .claude/scripts/check.sh
```

**Worker count**: `-n 8` is safe for most machines. CI uses `-n auto` (scales to core count). If PG connections exhaust on high-core machines, lower the number.

### Commit Gate

Tests MUST pass before any commit. A pre-commit hook enforces this automatically — it runs `bash .claude/scripts/check.sh` and blocks the commit on failure.

```bash
# Install the hook (once per clone):
cp .claude/scripts/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

If a test fails, fix it — do NOT bypass with `--no-verify`. Run the full suite for the affected domain, not just a single file. New features require tests (unit + integration as appropriate).

## Linting & Type Checking

- **ruff**: zero errors (`uv run ruff check .`)
- **pyright**: zero errors in app code (third-party type issues may be warnings)
- Config in `backend/pyproject.toml` under `[tool.ruff]` and `[tool.pyright]`

## Workflow Preferences

- Communicate in Chinese (user preference).
- Bug auditing: focus on real runtime bugs (crashes, data corruption, security, incorrect behavior). Do not report style issues or theoretical concerns.
- Commit messages in English, concise, focused on "why".
- After making changes, always run `task check` to verify.
- After `git pull`, run `bash .claude/scripts/post-pull.sh` or `task be:deps:sync && task be:db:migrate`.
- **CLAUDE.md ↔ .claude/**: these are peer project specifications. When updating conventions, scripts, agents, skills, or reference docs in one, sync the other immediately. See `.claude/reference/sync-rule.md` for the full checklist.
- **All commits go through PR**: never commit directly to main. Always create a feature branch, commit there, and open a pull request. This ensures code review happens before merge.
