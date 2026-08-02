# Project Conventions

Monorepo: `backend/` (Python/FastAPI) + `frontend/` (Vue 3) + `e2e/` (Playwright). See `README.md` for setup and commands.

## .claude/ Directory Structure

```
.claude/
├── agents/
│   └── check-runner.md                   # Background test runner (parallel to review)
├── skills/                               # on-demand skills (dir form: <name>/SKILL.md)
│   ├── cheese-py-code-review/SKILL.md    # Code review checklist + workflow (parallel & serial)
│   ├── post-pull/SKILL.md                # Post-git-pull checks (migrations, deps, health)
│   └── lark-{doc,drive,markdown,shared,wiki}  # → .agents/skills/… (vendored, see skills-lock.json)
├── scripts/
│   ├── check.sh                          # ruff + pyright + pytest, one command
│   ├── post-pull.sh                      # Post-git-pull checks (migrations, deps, health)
│   └── pre-commit                        # Copy to .git/hooks/ to block commits on check failure
└── settings.json                         # Permissions allowlist for common commands
```

Procedural guidance lives in **skills** (loaded on demand), not always-on prose:
review → `cheese-py-code-review`, post-pull → `post-pull`. This file holds the
always-relevant project conventions below.

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
- **CLAUDE.md ↔ .claude/ stay in sync** (peer project specs — the highest-priority rule): any change to one must be mirrored in the other, in the same change.
  - New/changed `.claude/skills|agents|scripts` → update the Directory Structure tree above.
  - Changed a convention here (Python/API/testing/datetime/security) → update the matching checklist in `cheese-py-code-review` skill.
  - Changed a check item in a skill → update the matching CLAUDE.md section.
  - Self-check after editing: does the tree list every subdir/file? Are the conventions identical on both sides?

## Documentation Map

Engineering docs live in `docs/` (git). **`docs/` is strictly for engineering documentation.** Non-engineering content (product proposals, marketing copy, operational plans, competition materials) must never be committed — stage in `tmp/` (gitignored) if Feishu is unavailable, then upload to the wiki when access is restored. Product-direction docs (vision, feedback) are canonical in the **team Feishu wiki** — no local copies are kept (staleness risk) — links in `docs/README.md`, fetch on demand via the lark skills. Real-name application materials live only in a restricted Feishu Drive folder, never in git.

To find or edit Feishu content, use the lark skills (`.claude/skills/lark-*`) and search at need (`lark-cli docs +search --profile cheese`) — do not maintain a static doc inventory here.

**lark-cli profile**: the team shares one Feishu org; all lark-cli Feishu operations must pass `--profile cheese` (app `cli_a97ca79454785bd5`, the only profile with drive/docs scopes). `no_token`/`403`/`no authority` almost always means a wrong profile, not a permission or cross-tenant problem. Setup, credential sync, and a troubleshooting table live in [`docs/feishu-lark.md`](docs/feishu-lark.md).
