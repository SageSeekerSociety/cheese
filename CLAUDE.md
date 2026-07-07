# Project Conventions

Monorepo: `backend/` (Python/FastAPI) + `frontend/` (Vue 3) + `e2e/` (Playwright). See `README.md` for setup and commands.

## Agent Layer (知是 2.0)

The AI-teammate layer on top of this backend is specified in
[`docs/design/architecture.md`](docs/design/architecture.md) — the single source of
truth for the data model, actor model, orchestration, connector plane and frontend.
Rules that MUST be followed when working on it:

- **一个 agent 就是一个用户.** One identity type (`user`). Authorship, work-item
  ownership and document edits are all `user_id`. Human vs agent differ only in login
  method (password/passkey vs a connector-minted session token). Business/domain code
  never branches on human-vs-agent. There is **no `is_agent` column** — agent-ness is
  *derived* (a user with a live execution binding / `agent_screen`), used only for
  presentation (an avatar badge), never for authz or business branching.
- **一套业务服务,三个前门.** Humans (REST), agents (tool/callback RPC) and the
  orchestrator all call the *same* domain services. The actor is injected at the trust
  boundary, never read from a request body. A valid session token is necessary, not
  sufficient — always authorize against the actor's real permissions.
- **Thin client.** The frozen `cli/` substrate is a single business-free `cheese` Go
  binary; command emission, the triage lock, attention policy and prompts live in the
  backend and are carried by a server-delivered cheeselet driver, so updates never
  require a client reinstall. `cli/` and its `link.Msg` wire protocol do not change as
  the agent layer grows (`misc/web-claude/` is the frozen reference contract).
- **万物皆块.** Chat and documents share one append-only `block` substrate with two
  trees (`reply_to_id`, `struct_parent_id`) + `block_ref`.
- **Chat is global / project-independent.** A `thread` (`domain/thread`) is a
  WeChat/Feishu-style group whose members are unrelated to any project (two members = a
  DM); `thread.project_id` and `block.project_id` are nullable. Adding an agent to a
  thread requires the agent owner's consent via `thread_membership_application` +
  notifications (mirrors `domain/team`).
- New *project-scoped* business domains anchor to `project_id` (documents, work items);
  name them to avoid the legacy `task` / `topics` domains (事项 is `domain/workitem`).
  Chat/threads are the deliberate exception — they are not project-anchored.
- **Backend is always the frontend origin + `/api`.** In every environment the API
  is reached at `<web-origin>/api/...` (prod example: `https://cheese.ruc.edu.cn/api/avatars/420`).
  The backend serves its routes at root (`/avatars/...`, `/connector/...`); the `/api`
  prefix is added by the edge (ingress in prod, the vite dev proxy in dev, which
  rewrites `^/api` → `` to `localhost:8080`). So the frontend's API base is `/api`, the
  connector WS is `<origin>/api/connector/session/{id}/screen`, and client artifacts are
  served from the same origin at `<origin>/connector/latest/<os>-<arch>/{cheese,tmux}`
  (see `docs/design/architecture.md` §5.5; `curl <origin>/connector/install.sh | sh`).
- **Connector enrollment is a device flow, not a hardcoded token.** `cheese auth login`
  runs the device flow (start/poll) and prints a short approve URL `<origin>/connect?…`;
  a logged-in human approves it there, binding the device to a project/agent user and
  minting a durable device token the client stores. Only then does `cheese link connect`
  (or `cheese link auto-connect`) connect; at runtime the device token is exchanged for
  per-connection session context. No `session_token` is ever written into config.
- **`cheese` installs itself as a cross-platform service.** `cheese link auto-connect`
  registers a boot service via `kardianos/service` (systemd/openrc/launchd); `cheese run`
  is the foreground entry the service manager calls; `cheese link status` /
  `cheese uninstall` manage it. Built for linux+darwin × amd64+arm64 (needs `tmux` + a
  unix pty; no Windows).

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
