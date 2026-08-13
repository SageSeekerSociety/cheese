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
(git pull 后的检查见 `post-pull` skill;迁移/后端测试/e2e 的领域坑见 `.claude/rules/`。)

## Review Workflow

1. First, understand what files changed (`git diff`, `git status`).
2. Read the changed files thoroughly.
3. Cross-check with `reference/cheese-backend-nt/` (Kotlin) or `reference/cheese-backend/` (NestJS) when behavior is unclear.
4. **MANDATORY**: Launch background check immediately after step 1.
   - Use the Agent tool with `subagent_type: "general-purpose"`, `run_in_background: true`, and the prompt:

     ```
     Run the project check script:
       bash .claude/scripts/check.sh
     Set Bash timeout to 20 minutes (1200000ms). Full tests take ~10-12 min.
     When done, report only:
     - PASS or FAIL per step (ruff / pyright / pytest)
     - If FAIL: which tests failed (last ~30 lines of pytest output)
     - If PASS: just say "All checks passed" — no more than 3 lines.
     ```

   - While it runs, continue with steps 2→3→5 (read files, cross-check, review).
   - When the agent reports back: if any step FAIL, report "Tests failed — commit blocked".
     If PASS, note "All checks passed" in the review summary.
5. Report findings grouped by severity. If step 4 failed, only report Critical: "Tests failed — commit blocked".

### 串行模式（无后台能力的 AI:Copilot / Cursor / Windsurf 等）

没有后台 agent 时,直接跑脚本(输出只有 ~10 行,不浪费 token),边等边读 diff:

```bash
bash .claude/scripts/check.sh   # 或 task check —— 输出 3 行 PASS/FAIL 结果
```

1. 先跑 `task check`(~30 秒起),等结果的同时读 `git diff` 看改动范围。
2. 收到结果后只 review 改动文件 + 对照规范。

**关键原则(两种模式通用):**
- 不要在测试跑完前下结论。
- 测试 FAIL 时,审查结论必须含 "Tests failed — commit blocked"。
- 代码质量 > 速度,但两者都要。

## Review Checklist

### 1. Conventions (canonical copies live in CLAUDE.md — already in context)

Check the diff against every rule in CLAUDE.md's **Architecture / Python
Conventions / API Design / Datetime / Security** sections. This skill does not
restate them; CLAUDE.md is the single source of truth.

### 2. Testing

- New code needs tests: `tests/unit/` for unit, `tests/integration/` for DB-backed. New API endpoints and services MUST have corresponding tests — untested code is not acceptable.
- `pytest.mark.anyio` for async tests. `SimpleNamespace` + `AsyncMock` for fakes.
- Test behavior, not source inspection.
- Auth helpers / fake-sync / shared-DB rules: `.claude/rules/backend-tests.md`.

### 3. Common Pitfalls

- **JWT timezone**: use `datetime.now(UTC)` aware — never `.replace(tzinfo=None)`.
- `AsyncSession` lifecycle handled by FastAPI `Depends(get_db)`.
- Meilisearch optional; fallback to PG FTS when `MEILISEARCH_URL` not set.

### 4. Reference Code Alignment

When unsure about expected behavior, consult `reference/`:
- `reference/cheese-backend-nt/` — Kotlin/Spring Boot (primary reference for teams, tasks, spaces).
- `reference/cheese-backend/` — NestJS/TypeScript (comments, materials, questions).
- `reference/cheese-frontend/` — Vue frontend (API contract expectations).

### 5. Common Mistakes — check EVERY diff (each entry is a real incident)

Semantic mistakes lint can't catch. Meta-patterns: **evidence before verdict**
(don't destroy or swallow error output, don't call something "flaky" without
the actual error), **existing ≠ wired** (an object/config/PR existing is not
the mechanism working), **bypassing the standard path breaks things**
(`--no-verify`, hand-run compose, partial checks).

1. **Behavior/signature changed, test doubles not updated.** Changing a
   function's signature, return value, or name → grep `backend/tests/` for
   every fake/mock/monkeypatch of it AND every test calling the affected
   route. (Three main-reddening incidents in one day.)
2. **New migration** → exactly one `alembic heads`, chained onto the current
   head (`.claude/rules/migrations.md`). Don't rely on the CI guard to catch
   it after the fact.
3. **Caches/artifacts in the diff.** Sanity-check the staged file COUNT; a
   cache directory once added 43k files. `git add -A` is never acceptable.
4. **"Wired up" claims need a consuming call site.** A setting, table, or
   function that nothing reads is not a feature (a binding table written but
   never read; a chooser with zero callers; a usage metric that was silently 0
   for days).
5. **Gate changes: verify the property, not the artifact.** If the goal is
   "red CI blocks X", the test is that a red actually blocks — not that a PR/
   check exists. A gate that skips a step must say so in its output ("green"
   must state what ran).
6. **Error evidence must survive.** `capture_output=True` without re-emitting
   on failure, `>>/dev/null`, or reset/cleanup before failure is recorded —
   Critical. You can't diagnose what you deleted.
7. **CI-affecting tests/workflows: confirm the job actually EXECUTED once**
   (not skipped by scope gates or paths filters). A suite once sat broken for
   days behind "green" runs that were skips.
8. **New comparison fields: define the missing-field case.** A staleness/drift
   check that treats "legacy object lacks the new field" as "drifted" wipes
   state on every old object (nearly shipped once: it would have silently
   reset every existing topic's container).

### 6. Output Format

1. **Critical** (must fix): bugs, security, data corruption, API contract mismatch, broken tests.
2. **Should fix**: missing tests/annotations, architecture violations.
3. **Nice to have**: performance, clarity.
4. **Ignore**: style nits (ruff covers), theoretical concerns without evidence.
