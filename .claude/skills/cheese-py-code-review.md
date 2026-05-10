---
name: cheese-py-code-review
description: >
  代码审查 cheese-backend-py 项目。审查架构分层(route→service→repository→model)、
  Python 3.11+ 规范、类型安全、测试覆盖、ruff/pyright/pytest、API 设计、
  安全性、常见陷阱(如 JWT 时区 bug)。同时对照 reference/ 目录的原始实现检查业务逻辑。
  TRIGGER when: 用户要求审查代码、检查改动、review PR、提交前检查；用户在 cheese-backend-py
  项目下说"帮我看下这段代码""有什么问题""审查一下""review"等。
  SKIP: 纯前端代码审查、非 Python 项目、非 cheese 项目。
---

# Code Review — Cheese Backend (Python)

Review changed code against cheese-backend-py project standards.

项目基础规范：@CLAUDE.md
前置检查清单（pull 后必读）：@reference/post-pull-checks.md
并行审查流程：@reference/parallel-review.md
同步规则（每次改前必读！）：@reference/sync-rule.md

## Review Workflow

1. First, understand what files changed (`git diff`, `git status`).
2. Read the changed files thoroughly.
3. Cross-check with `reference/cheese-backend-nt/` (Kotlin) or `reference/cheese-backend/` (NestJS) when behavior is unclear.
4. **MANDATORY**: Launch check-runner agent in background immediately after step 1.
   - Use the Agent tool with `subagent_type: "check-runner"` and `run_in_background: true`.
   - While it runs, continue with steps 2→3→5 (read files, cross-check, review).
   - When the agent reports back: if any step FAIL, report "Tests failed — commit blocked".
     If PASS, note "All checks passed" in the review summary.
   - Timeout is 20 minutes — the agent handles this.
5. Report findings grouped by severity. If step 4 failed, only report Critical: "Tests failed — commit blocked".

## Review Checklist

### 1. Architecture & Layering

```
Route → Service → Repository → Model
(app/api/routes/) → (app/domain/**/services.py) → (app/domain/**/repositories.py) → (app/domain/**/models.py)
```

- Routes: only parameter parsing, DI, call service, return response. No business logic.
- Services: all business logic, cross-domain coordination, validation beyond schema.
- Repositories: only data access. No business logic.
- Models: SQLAlchemy 2.0 style (`mapped_column`, `Mapped[]`).

### 2. Python Conventions

- >=3.11: use `list[str]`, `dict[int, str]`, `X | None`. No `from __future__ import annotations`.
- No method named `list`, `set`, `dict`, `type`.
- Async/await consistently; `AsyncSession` for DB.
- Dependency injection via FastAPI `Depends()`.

### 3. Type Safety

- All function signatures annotated. No `Any` unless external boundary.
- Pydantic v2 for request/response schemas.

### 4. Testing

- New code needs tests: `tests/unit/` for unit, `tests/integration/` for DB-backed. New API endpoints and services MUST have corresponding tests — untested code is not acceptable.
- `pytest.mark.anyio` for async tests. `SimpleNamespace` + `AsyncMock` for fakes.
- Test behavior, not source inspection.

### 5. API Design

- RESTful: GET/POST/PUT/DELETE on `/resource`.
- Pagination: `pageStart` + `pageSize`, return `{data: [...], total: int}`.
- Errors: use `app.core.errors` classes, not raw `HTTPException`.
- Auth: `Depends(require_auth_user)` for protected; `Depends(get_auth_user)` for public-aware.
- Response format: `{"code": 200, "message": "...", "data": {...}}`.

### 6. Security

- Validate all input via Pydantic schemas.
- SQLAlchemy ORM for queries (parameterized). Raw SQL must use `text()` with bind params.
- JWT: `Authorization: Bearer <token>`, check `type` claim.
- No hardcoded secrets; use `app.core.config.settings`.

### 7. Common Pitfalls

- **JWT timezone**: use `datetime.now(UTC)` aware — never `.replace(tzinfo=None)`.
- `AsyncSession` lifecycle handled by FastAPI `Depends(get_db)`.
- Meilisearch optional; fallback to PG FTS when `MEILISEARCH_URL` not set.

### 8. Reference Code Alignment

When unsure about expected behavior, consult `reference/`:
- `reference/cheese-backend-nt/` — Kotlin/Spring Boot (primary reference for teams, tasks, spaces).
- `reference/cheese-backend/` — NestJS/TypeScript (comments, materials, questions).
- `reference/cheese-frontend/` — Vue frontend (API contract expectations).

### 9. Output Format

1. **Critical** (must fix): bugs, security, data corruption, API contract mismatch, broken tests.
2. **Should fix**: missing tests/annotations, architecture violations.
3. **Nice to have**: performance, clarity.
4. **Ignore**: style nits (ruff covers), theoretical concerns without evidence.

### 10. Keep in Sync with CLAUDE.md

This skill and `CLAUDE.md` are peer project specifications. When updating conventions, testing rules, timezone settings, or workflow preferences, update BOTH files. They must stay consistent.
