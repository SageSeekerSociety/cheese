# Project Conventions

## Python version
- Python version is managed by uv (pinned in `.python-version`). Currently >=3.11.
- Do NOT use `from __future__ import annotations`. The project targets Python 3.11+ where `list[str]`, `dict[int, str]`, `X | None` etc. work natively in annotations.
- Do NOT name methods `list`, `set`, `dict`, `type`, or other builtin names — they shadow builtins in class scope and break type annotations.
- Use quoted strings (`"PermissionRule"`) only for genuine forward references (e.g., self-referencing class in a `@staticmethod` return type).

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

## Linting & Type Checking

- **ruff**: `uv run ruff check .` — must pass with zero errors.
- **pyright**: `uv run pyright` — must have zero errors in app code (warnings are acceptable for third-party library type issues).
- Config is in `pyproject.toml` under `[tool.ruff]` and `[tool.pyright]`.

## Workflow preferences

- Communicate in Chinese (user preference).
- When auditing for bugs, focus on **real runtime bugs** (crashes, data corruption, security, incorrect behavior). Do not report style issues or theoretical concerns.
- Always run `ruff check`, `pytest`, and `pyright` after making changes to verify nothing is broken.
- Commit messages in English, concise, focused on "why".