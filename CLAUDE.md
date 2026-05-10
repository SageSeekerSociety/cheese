# Project Conventions

## Operating System

This project targets **Linux / macOS**. All scripts, venv paths, and toolchain assume a Unix environment.

**Windows users**: do NOT run `uv sync`, `uv run`, or any Python commands directly on the host. Use Docker instead:

```bash
docker compose up -d                          # start all services (DB, Redis, backend)
docker compose exec cheese_py <command>       # run commands inside the container
docker compose logs cheese_py                 # view backend logs
docker compose restart cheese_py              # restart backend
```

The Docker container mounts the project directory as a volume, so file changes on the host are immediately reflected inside the container. Code editing can be done on Windows; execution must happen in Docker.

## Python version
- Python version is managed by uv (pinned in `.python-version`). Currently >=3.11.
- Do NOT use `from __future__ import annotations`. The project targets Python 3.11+ where `list[str]`, `dict[int, str]`, `X | None` etc. work natively in annotations.
- Do NOT name methods `list`, `set`, `dict`, `type`, or other builtin names — they shadow builtins in class scope and break type annotations.
- Use quoted strings (`"PermissionRule"`) only for genuine forward references (e.g., self-referencing class in a `@staticmethod` return type).

## Datetime convention
- All DB columns use `DateTime(timezone=True)` (PostgreSQL `TIMESTAMPTZ`). Always pass `datetime.now(UTC)` (timezone-aware) — never `.replace(tzinfo=None)`.

## Reference code

The `reference/` directory contains the original implementations this project is migrated from. Always consult them when unclear about expected behavior, API contracts, or business logic:

- `reference/cheese-backend/` — **NestJS (TypeScript)** original backend (comments, materials, answers, questions, etc.)
- `reference/cheese-backend-nt/` — **Kotlin (Spring Boot)** backend (teams, tasks, spaces, notifications, auth, AI, etc.)
- `reference/cheese-frontend/` — **Vue** frontend (useful for understanding API contract expectations)

When fixing bugs or implementing features, cross-reference with the corresponding reference code to verify correctness.

## Testing

- Write **normal functional tests**, not regression-style tests. Test actual behavior (mock dependencies, call the function, assert outputs), not source code inspection (`inspect.getsource` is not acceptable).
- Use `pytest.mark.anyio` for async tests.
- Use `SimpleNamespace` for lightweight fakes, `AsyncMock`/`MagicMock` for repository/service mocks.
- Test files go in `tests/unit/` for unit tests, `tests/contract/` for API contract comparisons, `tests/integration/` for DB-backed tests.
- Run tests: `uv run python -m pytest tests/unit/ -q`

**Commit gate**: tests MUST pass before any commit. If a test fails, fix it first — do NOT bypass. Run the full suite for the affected domain, not just a single file.

**New features require tests**: when adding a new API endpoint, service, or domain feature, write corresponding tests (unit + integration as appropriate). Do NOT submit untested code.

On Windows (Docker): `docker compose exec cheese_py sh -c "cd /app && uv run python -m pytest tests/ -q"`

**Test timeout**: full test suite takes ~10-12 minutes. Always set Bash timeout to at least **20 minutes** (1200000ms) when running the full suite. Integration tests hit real databases and are slow — do not abort early.

## Linting & Type Checking

- **ruff**: `uv run ruff check .` — must pass with zero errors.
- **pyright**: `uv run pyright` — must have zero errors in app code (warnings are acceptable for third-party library type issues).
- Config is in `pyproject.toml` under `[tool.ruff]` and `[tool.pyright]`.

## Workflow preferences

- Communicate in Chinese (user preference).
- When auditing for bugs, focus on **real runtime bugs** (crashes, data corruption, security, incorrect behavior). Do not report style issues or theoretical concerns.
- Always run `ruff check`, `pytest`, and `pyright` after making changes to verify nothing is broken.
- **Commit gate**: tests must pass. Block the commit if any test fails — no exceptions.
- Commit messages in English, concise, focused on "why".
- **CLAUDE.md ↔ .claude/skills/**: these two are peer project specifications. When updating conventions, testing rules, timezone settings, or workflow preferences in one, sync the other immediately.