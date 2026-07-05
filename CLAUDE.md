# Project Conventions

Monorepo: `backend/` (Python/FastAPI) + `frontend/` (Vue 3) + `e2e/` (Playwright) + `connector/` (client-machine components). See `README.md` for setup and commands.

## Cheese Agent Layer

The platform embeds an AI teammate (芝士) that participates in every project through a coding agent running on client machines. Full design: **`docs/design/cheese-agent-layer.md`** (self-contained). Non-negotiable rules when working on this layer:

- Platform senses the agent **only through structured tool calls**. Never regex-parse the agent's natural-language output.
- The agent's natural output belongs to "现场" (hidden by default). All outward communication goes through tools.
- A tool = an annotated Python function on the backend; schema/CLI/permission/validation all project from the function. Keep **agent-supplied args separate from injected context** (`actor`/`topic`/`session`) — the actor never comes from an agent arg.
- Authorize every tool RPC at the **orchestrator → business-function** boundary, against the agent actor's real permissions. `session_token` = identity only, sent in a header, scoped + short-TTL. Never trust a valid token alone.
- `connector/` is a thin, generic, **versioned** client host: keep all volatile logic (tools, prompts, roles, models, detection rules) fetched from the backend; the connector↔backend protocol is a **frozen public API** — additive-only, backward-compatible. Avoid changes that force clients to update.
- Orchestrator ↔ business layer communicate by **direct function calls** (no event bus). One **serial queue per topic** — the same topic never runs two agent turns concurrently.
- Blocks are **append-only**; editing a doc = issuing an instruction; results **require human acceptance**.
- New topic model is **namespace-isolated** from the existing tag-style `topic`.

## .claude/ Directory Structure

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

## Development Commands

Backend runs locally via `uv run` from `backend/`. Infrastructure (PG, Valkey, ES) in Docker. Use Taskfile:

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
- **CLAUDE.md ↔ .claude/**: these are peer project specifications. When updating one, sync the other. See `.claude/reference/sync-rule.md`.
- **All commits go through PR**: never commit directly to main.
