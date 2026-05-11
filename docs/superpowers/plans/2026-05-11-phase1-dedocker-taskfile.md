# Phase 1: De-dockerize Development + Taskfile

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the `cheese_py` Docker service from the development workflow and replace `docker compose exec` scripts with direct `uv run` commands, then add Taskfile as the unified task runner.

**Architecture:** The Python backend runs directly on the host via `uv run` instead of inside a Docker container. Infrastructure services (PostgreSQL, Valkey, Elasticsearch) remain in Docker. Taskfile provides a unified CLI for all development tasks (`task check`, `task test`, `task dev`, etc.) with parallel execution and file-change caching.

**Tech Stack:** uv (Python), Taskfile (go-task), Docker Compose (infrastructure only), bash

---

## Scope

This plan does NOT restructure the repo into `backend/` + `frontend/`. That is Phase 2. This plan operates on the current flat structure and produces a fully working local development workflow.

## File Map

| Action | File | Purpose |
|--------|------|---------|
| Modify | `docker-compose.yml` | Remove `cheese_py` service, keep PG/Valkey/ES |
| Modify | `.claude/scripts/check.sh` | Replace `docker compose exec` with direct `uv run` |
| Modify | `.claude/scripts/post-pull.sh` | Replace `docker compose exec` with direct `uv run` |
| Modify | `.claude/settings.json` | Update permission allowlist (remove docker exec, add task commands) |
| Modify | `CLAUDE.md` | Update workflow docs: local dev, Taskfile commands, remove Docker instructions |
| Modify | `.claude/skills/cheese-py-code-review.md` | Update check script reference if needed |
| Modify | `.claude/reference/parallel-review.md` | Update timing/command references |
| Modify | `.claude/reference/post-pull-checks.md` | Update to reflect local commands |
| Modify | `.gitignore` | Add `.task/` (Taskfile state dir) |
| Create | `Taskfile.yml` | Root Taskfile — compose backend tasks + infra lifecycle |
| Create | `.env.example` | Copy of `.env` with empty secrets (for onboarding docs) |

---

### Task 1: Remove `cheese_py` Docker service

**Files:**
- Modify: `docker-compose.yml:98-124` (remove cheese_py service)

- [ ] **Step 1: Remove the cheese_py service from docker-compose.yml**

Keep postgres, postgres_test, valkey, valkey_test, elasticsearch, networks, and volumes. Remove only the `cheese_py` service block (lines 98-124). Also remove the `uploads` volume from the `volumes:` section at the bottom since it was only used by cheese_py.

```yaml
# Remove this entire block (lines 98-124):
  # Python backend (this project)
  cheese_py:
    build:
      context: .
      dockerfile: Dockerfile
      target: development
    container_name: cheese_py_backend
    ports:
      - "8081:8081"
    environment:
      - TZ=Asia/Shanghai
      - ENVIRONMENT=development
      - DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/cheese
      - REDIS_URL=redis://valkey:6379/0
    env_file:
      - .env
    volumes:
      - .:/app
      - uploads:/app/uploads
    depends_on:
      postgres:
        condition: service_healthy
      valkey:
        condition: service_healthy
    command: ["sh", "-c", "uv run alembic upgrade head && uv run uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload"]
    networks:
      - cheese_py_network

# Also remove from volumes: section at the bottom:
  uploads:
```

- [ ] **Step 2: Verify Docker Compose still starts infrastructure**

Run: `docker compose up -d`
Expected: postgres, postgres_test, valkey, valkey_test, elasticsearch start. No cheese_py service.

Run: `docker compose ps`
Expected: 5 containers running, no cheese_py_backend.

- [ ] **Step 3: Verify backend runs locally**

Run: `uv sync --extra dev --extra test && uv run uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload`
Expected: Server starts on port 8081. Hit `curl -sf http://localhost:8081/healthz` to confirm.
Stop the server with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git checkout -b feat/dedocker-taskfile
git add docker-compose.yml
git commit -m "$(cat <<'EOF'
refactor: remove cheese_py Docker service from dev workflow

Backend now runs directly on the host via uv run.
Infrastructure services (PG, Valkey, ES) remain in Docker.
Dockerfile kept for production builds.
EOF
)"
```

---

### Task 2: Update scripts to use direct `uv run`

**Files:**
- Modify: `.claude/scripts/check.sh`
- Modify: `.claude/scripts/post-pull.sh`

- [ ] **Step 1: Rewrite check.sh**

Replace the entire file with:

```bash
#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:  run entire suite (no testmon, ~25s)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

PASS=0
FAIL=0

PYTEST_EXTRA="--testmon"
if [[ "${1:-}" == "--full" ]]; then
    PYTEST_EXTRA=""
    echo "Mode: FULL suite"
else
    echo "Mode: INCREMENTAL (--testmon)"
fi

# --- ruff ---
echo "==> ruff check"
if uv run ruff check . 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if uv run pyright 2>&1 | tail -2; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
echo "==> pytest"
if uv run pytest tests/ -n 8 $PYTEST_EXTRA -q --ignore=tests/contract 2>&1 | tail -3; then
    echo "  PASS: pytest"
    ((++PASS))
else
    echo "  FAIL: pytest"
    ((++FAIL))
fi

# --- summary ---
echo ""
echo "Result: $PASS/$((PASS+FAIL)) passed"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
```

- [ ] **Step 2: Rewrite post-pull.sh**

Replace the entire file with:

```bash
#!/usr/bin/env bash
# post-pull.sh — checks to run after git pull
# Usage: bash .claude/scripts/post-pull.sh
set -euo pipefail

echo "=== Post-pull checks ==="

# 1. New migrations
echo ""
echo "==> New migrations"
if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q "migrations/versions/"; then
    echo "  NEW migrations detected:"
    git diff --name-only HEAD@{1} HEAD | grep "migrations/versions/"
else
    echo "  No new migrations."
fi

# 2. Apply migrations
echo ""
echo "==> Alembic upgrade"
uv run alembic upgrade head
echo "  OK: migrations up to date."

# 3. Dependency changes
echo ""
echo "==> Dependencies"
if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -qE "pyproject.toml|uv.lock"; then
    echo "  pyproject.toml or uv.lock changed — syncing..."
    uv sync --extra dev --extra test
    echo "  OK: dependencies synced."
else
    echo "  No dependency changes."
fi

# 4. Health check
echo ""
echo "==> Health check"
if curl -sf http://localhost:8081/healthz > /dev/null 2>&1; then
    echo "  OK: server responding."
else
    echo "  SKIP: server not running."
fi

echo ""
echo "=== Done ==="
```

- [ ] **Step 3: Run check.sh to verify it works without Docker**

Run: `bash .claude/scripts/check.sh`
Expected: ruff PASS, pyright PASS, pytest PASS. Result: 3/3 passed.

- [ ] **Step 4: Commit**

```bash
git add .claude/scripts/check.sh .claude/scripts/post-pull.sh
git commit -m "$(cat <<'EOF'
refactor: update scripts to use direct uv run instead of docker exec
EOF
)"
```

---

### Task 3: Install Taskfile and create root Taskfile.yml

**Files:**
- Create: `Taskfile.yml`
- Modify: `.gitignore`

- [ ] **Step 1: Verify Taskfile is installed**

Run: `task --version 2>/dev/null || echo "NOT INSTALLED"`

If not installed, install:
```bash
sh -c "$(curl --location https://taskfile.dev/install.sh)" -- -d -b /usr/local/bin
```

Or via npm: `npm install -g @go-task/cli`

Verify: `task --version`
Expected: `Task version: v3.x.x`

- [ ] **Step 2: Add `.task/` to .gitignore**

Append to `.gitignore`:

```
.task/
```

- [ ] **Step 3: Create Taskfile.yml**

```yaml
version: '3'

dotenv: ['.env']

vars:
  PYTEST_WORKERS: 8

tasks:
  # ── Development ────────────────────────────────────────
  dev:
    desc: Start backend dev server (+ infrastructure)
    deps: [infra]
    cmds:
      - uv run uvicorn app.main:app --host 0.0.0.0 --port 8081 --reload

  # ── Code Quality ───────────────────────────────────────
  lint:
    desc: Run ruff linter
    cmds:
      - uv run ruff check .
    sources:
      - app/**/*.py
      - tests/**/*.py
      - pyproject.toml

  lint:fix:
    desc: Run ruff with auto-fix
    cmds:
      - uv run ruff check . --fix

  fmt:
    desc: Format code with ruff
    cmds:
      - uv run ruff format .

  typecheck:
    desc: Run pyright type checker
    cmds:
      - uv run pyright
    sources:
      - app/**/*.py
      - pyproject.toml

  # ── Testing ────────────────────────────────────────────
  test:
    desc: Run tests (incremental via testmon)
    cmds:
      - uv run pytest tests/ -n {{.PYTEST_WORKERS}} --testmon -q --ignore=tests/contract

  test:full:
    desc: Run full test suite (no testmon)
    cmds:
      - uv run pytest tests/ -n {{.PYTEST_WORKERS}} -q --ignore=tests/contract

  test:failed:
    desc: Re-run only last-failed tests
    cmds:
      - uv run pytest tests/ --lf -n {{.PYTEST_WORKERS}} -q

  # ── All-in-one ─────────────────────────────────────────
  check:
    desc: Run all checks (ruff + pyright + pytest)
    cmds:
      - task: lint
      - task: typecheck
      - task: test

  check:full:
    desc: Run all checks with full test suite
    cmds:
      - task: lint
      - task: typecheck
      - task: test:full

  # ── Database ───────────────────────────────────────────
  db:migrate:
    desc: Run alembic migrations
    cmds:
      - uv run alembic upgrade head

  db:revision:
    desc: Create a new migration (set MSG="description")
    cmds:
      - uv run alembic revision --autogenerate -m "{{.MSG}}"
    requires:
      vars: [MSG]

  db:downgrade:
    desc: Downgrade one migration step
    cmds:
      - uv run alembic downgrade -1

  # ── Infrastructure ─────────────────────────────────────
  infra:
    desc: Start infrastructure services (PG, Valkey, ES)
    cmds:
      - docker compose up -d
    status:
      - docker compose ps --status running --format '{{`{{.Name}}`}}' | grep -q cheese_py_postgres

  infra:down:
    desc: Stop infrastructure services
    cmds:
      - docker compose down

  infra:logs:
    desc: Tail infrastructure logs
    cmds:
      - docker compose logs -f {{.CLI_ARGS}}

  infra:ps:
    desc: Show running infrastructure containers
    cmds:
      - docker compose ps

  # ── Dependencies ───────────────────────────────────────
  deps:sync:
    desc: Sync Python dependencies
    cmds:
      - uv sync --extra dev --extra test
    sources:
      - pyproject.toml
      - uv.lock
```

- [ ] **Step 4: Verify Taskfile works**

Run: `task --list`
Expected: All tasks listed with descriptions.

Run: `task check`
Expected: lint PASS, typecheck PASS, test PASS.

Run: `task db:migrate`
Expected: Alembic runs migrations against local PG.

- [ ] **Step 5: Commit**

```bash
git add Taskfile.yml .gitignore
git commit -m "$(cat <<'EOF'
feat: add Taskfile as unified task runner

Provides task check, task test, task dev, task db:migrate, etc.
Replaces ad-hoc uv run commands with a consistent CLI.
Includes file-change caching via sources: to skip unchanged checks.
EOF
)"
```

---

### Task 4: Update .claude/ configuration

**Files:**
- Modify: `.claude/settings.json`
- Modify: `.claude/skills/cheese-py-code-review.md`
- Modify: `.claude/reference/parallel-review.md`
- Modify: `.claude/reference/post-pull-checks.md`
- Modify: `.claude/agents/check-runner.md`

- [ ] **Step 1: Update .claude/settings.json**

Replace with:

```json
{
  "permissions": {
    "allow": [
      "Bash(bash .claude/scripts/check.sh *)",
      "Bash(bash .claude/scripts/post-pull.sh *)",
      "Bash(task *)",
      "Bash(uv run *)",
      "Bash(uv sync *)",
      "Bash(docker compose up *)",
      "Bash(docker compose down *)",
      "Bash(docker compose logs *)",
      "Bash(docker compose ps *)",
      "Bash(git diff *)",
      "Bash(git status *)",
      "Bash(git log *)",
      "Bash(git add *)",
      "Bash(git commit *)",
      "Bash(curl -sf http://localhost:8081 *)",
      "Bash(ls *)",
      "Bash(mkdir *)"
    ]
  }
}
```

- [ ] **Step 2: Update check-runner.md agent**

Replace the command in `.claude/agents/check-runner.md`:

```markdown
---
name: check-runner
description: >
  Run ruff + pyright + pytest via check.sh. Run this in background while
  doing code review work in parallel. Reports concise pass/fail summary.
tools: Bash, Read
model: sonnet
---

Run the project check script:

```bash
bash .claude/scripts/check.sh
```

Set Bash timeout to **5 minutes** (300000ms). Local checks take ~25s full, ~10s incremental.

When done, report only:
- PASS or FAIL per step (ruff / pyright / pytest)
- If FAIL: which tests failed (last ~30 lines of pytest output)
- If PASS: just say "All checks passed" — no more than 3 lines.
```

- [ ] **Step 3: Update parallel-review.md**

Replace the timing references in `.claude/reference/parallel-review.md`. Change "~10 分钟" to "~30 秒", and update the serial mode section:

```markdown
---
name: parallel-review
description: 审查流程 — 支持并行（Claude Code）和串行（其他 AI），输出简洁
---

# Review Workflow

无论用什么 AI 工具，审查流程的目标一致：代码质量 > 速度，但两者都要。

## 模式 A：并行（Claude Code 专用）

后台跑测试，前台同步读代码，互不阻塞。

```
Step 1: git diff / git status
         │
         ├──→ 后台启动 general-purpose agent（check-runner prompt）
         │    run_in_background: true, timeout: 5min
         │
         └──→ Step 2→3: 读改动文件、对照 reference/
              读完后若 agent 未完成，继续深入审查
                    ↓
              收到 agent 报告 → 汇总输出
```

## 模式 B：串行（所有 AI 通用）

没有后台能力的 AI（Copilot、Cursor、Windsurf 等）直接用脚本，输出足够短不会浪费 token：

```bash
bash .claude/scripts/check.sh
# 或
task check
```

脚本只输出 3 行结果：

```
==> ruff check
  PASS: ruff
==> pyright
  PASS: pyright
==> pytest
  PASS: pytest
Result: 3/3 passed
```

**审查流程：**
1. 先跑 `task check`（~30 秒）
2. 等结果的同时读 `git diff` 看改动范围
3. 收到结果后，只 review 改动文件 + 对照规范
4. 汇总输出

## 关键原则

- 检查脚本输出极简（~10 行），任何 AI 都能快速理解。
- 测试 FAIL 时，审查结论必须包含 "Tests failed — commit blocked"。
- 不要在测试跑完前下结论。
```

- [ ] **Step 4: Update post-pull-checks.md**

Replace `.claude/reference/post-pull-checks.md`:

```markdown
---
name: post-pull-checks
description: git pull 之后必须执行的检查清单
---

# Post-Pull Checks

每次 `git pull` 后执行：

```bash
bash .claude/scripts/post-pull.sh
# 或
task deps:sync && task db:migrate
```

该脚本自动完成：迁移检查、alembic 升级、依赖变更检测、健康检查。

## 注意事项

- 迁移失败时不要回滚：优先前滚修复（forward fix），避免数据丢失。
- 如有大量数据操作（如加索引、NOT NULL 约束），先在 staging 环境验证耗时。
```

- [ ] **Step 5: Commit**

```bash
git add .claude/settings.json .claude/agents/check-runner.md .claude/reference/parallel-review.md .claude/reference/post-pull-checks.md
git commit -m "$(cat <<'EOF'
refactor: update .claude/ config for local dev workflow

- settings.json: add task/uv permissions, remove docker exec
- check-runner: reduce timeout from 20min to 5min (local is fast)
- parallel-review: update timing estimates
- post-pull-checks: add Taskfile alternative commands
EOF
)"
```

---

### Task 5: Update CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update the Operating System section**

Find the `## Operating System` section and replace it with:

```markdown
## Operating System

This project targets **Linux / macOS**. Python runs **directly on the host** via `uv run`. Infrastructure services (PostgreSQL, Valkey, Elasticsearch) run in Docker:

```bash
docker compose up -d                          # start infrastructure (DB, Redis, ES)
docker compose ps                             # check infrastructure status
docker compose logs -f postgres               # view specific service logs
docker compose down                           # stop infrastructure
```

The backend runs locally:

```bash
uv sync --extra dev --extra test              # install dependencies (first time / after lock change)
uv run uvicorn app.main:app --port 8081 --reload  # start dev server
uv run pytest tests/ -n 8 --testmon -q        # run tests
```

Or use Taskfile (recommended):

```bash
task infra                                    # start infrastructure
task dev                                      # start backend (starts infra automatically)
task check                                    # ruff + pyright + pytest
task --list                                   # see all available tasks
```

**Important**: Dockerfile is kept for **production builds only**. Do NOT use `docker compose exec` for development.
```

- [ ] **Step 2: Update the Pre-written Scripts section**

Replace:

```markdown
## Pre-written Scripts

Instead of typing long commands, use Taskfile or the scripts in `.claude/scripts/`:

```bash
task check                          # ruff + pyright + pytest (recommended)
task test                           # incremental tests only
task lint                           # ruff only
task db:migrate                     # alembic upgrade head

# Or use scripts directly:
bash .claude/scripts/check.sh       # ruff + pyright + pytest, prints "3/3 passed" or failures
bash .claude/scripts/post-pull.sh   # migration check, alembic upgrade, dep sync, health check
bash .claude/scripts/pre-commit     # install as .git/hooks/pre-commit to block commits on check failure
```
```

- [ ] **Step 3: Update the .claude/ Directory Structure section**

Replace the tree in the `.claude/ Directory Structure` section to add Taskfile reference:

```markdown
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

Root-level `Taskfile.yml` provides the unified task runner interface (`task check`, `task dev`, `task test`, etc.). See `task --list` for all commands.
```

- [ ] **Step 4: Update the Running Tests section**

Replace the testing command examples:

```markdown
### Running Tests

```bash
# Full suite (parallel, ~25s locally):
task test:full
# Or: uv run pytest tests/ -n 8 -q

# Incremental (only tests affected by your changes, ~5-10s):
task test
# Or: uv run pytest tests/ -n 8 --testmon -q

# Only last-failed (TDD loop):
task test:failed
# Or: uv run pytest tests/ --lf -n 8 -q

# Full check (ruff + pyright + pytest):
task check
# Or: bash .claude/scripts/check.sh
```
```

- [ ] **Step 5: Update Workflow Preferences**

In the `## Workflow Preferences` section, change:

```
- After making changes, always run `bash .claude/scripts/check.sh` to verify.
- After `git pull`, run `bash .claude/scripts/post-pull.sh`.
```

to:

```
- After making changes, always run `task check` to verify.
- After `git pull`, run `bash .claude/scripts/post-pull.sh` or `task deps:sync && task db:migrate`.
```

- [ ] **Step 6: Verify CLAUDE.md consistency**

Read through the full CLAUDE.md and confirm no remaining references to `docker compose exec cheese_py`. Search for:

Run: `grep -n "docker compose exec\|cheese_py" CLAUDE.md`
Expected: No matches (docker compose references should only be for infrastructure).

- [ ] **Step 7: Commit**

```bash
git add CLAUDE.md
git commit -m "$(cat <<'EOF'
docs: update CLAUDE.md for local dev workflow with Taskfile

- Replace Docker-based dev instructions with direct uv run
- Add Taskfile commands as primary interface
- Update test running examples
- Keep Docker for infrastructure services only
EOF
)"
```

---

### Task 6: Create .env.example and fix pyproject.toml dev deps

**Files:**
- Create: `.env.example`
- Modify: `pyproject.toml` (consolidate dev dependencies)

- [ ] **Step 1: Create .env.example**

Copy `.env` to `.env.example`, replacing secret values with placeholders:

```bash
cp .env .env.example
```

Then edit `.env.example`: replace actual secret values with empty strings or placeholder text, keeping the structure and comments. The current `.env` already uses placeholder values, so this is mostly a copy. Verify no real secrets are present.

- [ ] **Step 2: Consolidate dev dependencies in pyproject.toml**

The current setup has dev tools split between `[project.optional-dependencies] dev` and `[dependency-groups] dev`. The optional-dependencies approach requires `--extra dev` which is easy to forget (this is why ruff/pyright weren't installed earlier). Move everything into `[dependency-groups]` which uv installs by default:

In `pyproject.toml`, find:

```toml
[project.optional-dependencies]
test = [
    "pytest>=8.0.0,<9.0.0",
    "pytest-asyncio>=0.23.0,<1.0.0",
    "pytest-rerunfailures>=14.0,<15.0",
    "anyio>=4.0.0,<5.0.0",
]
dev = [
    "ruff>=0.15.0,<0.16.0",
    "pyright>=1.1.0,<2.0.0",
]
```

and:

```toml
[dependency-groups]
dev = [
    "playwright>=1.58.0",
    "pytest-cov>=7.0.0",
    "pytest-testmon>=2.2.0",
]
```

Replace both with a single `[dependency-groups]` block and remove `[project.optional-dependencies]` entirely:

```toml
[dependency-groups]
dev = [
    "ruff>=0.15.0,<0.16.0",
    "pyright>=1.1.0,<2.0.0",
    "pytest>=8.0.0,<9.0.0",
    "pytest-asyncio>=0.23.0,<1.0.0",
    "pytest-rerunfailures>=14.0,<15.0",
    "anyio>=4.0.0,<5.0.0",
    "pytest-cov>=7.0.0",
    "pytest-testmon>=2.2.0",
    "playwright>=1.58.0",
]
```

- [ ] **Step 3: Re-sync and verify**

Run: `uv sync`
Expected: All dev dependencies installed (ruff, pyright, pytest, etc.) without needing `--extra`.

Run: `uv run ruff --version && uv run pyright --version`
Expected: Both tools available.

- [ ] **Step 4: Update Taskfile deps:sync command**

In `Taskfile.yml`, change the `deps:sync` task command from:

```yaml
  deps:sync:
    desc: Sync Python dependencies
    cmds:
      - uv sync --extra dev --extra test
```

to:

```yaml
  deps:sync:
    desc: Sync Python dependencies
    cmds:
      - uv sync
```

- [ ] **Step 5: Update post-pull.sh**

In `.claude/scripts/post-pull.sh`, change `uv sync --extra dev --extra test` to `uv sync`.

- [ ] **Step 6: Run full check to verify nothing broke**

Run: `task check:full`
Expected: All checks pass.

- [ ] **Step 7: Commit**

```bash
git add .env.example pyproject.toml uv.lock Taskfile.yml .claude/scripts/post-pull.sh
git commit -m "$(cat <<'EOF'
chore: consolidate dev deps into dependency-groups, add .env.example

Move ruff/pyright/pytest from [project.optional-dependencies] into
[dependency-groups] dev so uv sync installs everything by default
without needing --extra flags.
EOF
)"
```

---

### Task 7: Smoke test the full workflow

This task verifies the entire development workflow end-to-end with no Docker backend.

- [ ] **Step 1: Stop all Docker containers**

Run: `docker compose down`

- [ ] **Step 2: Start only infrastructure**

Run: `task infra`
Expected: PG, Valkey, ES containers start.

Run: `docker compose ps`
Expected: postgres, postgres_test, valkey, valkey_test, elasticsearch running. No cheese_py.

- [ ] **Step 3: Run migrations**

Run: `task db:migrate`
Expected: Alembic upgrade succeeds.

- [ ] **Step 4: Run full check suite**

Run: `task check:full`
Expected: ruff PASS, pyright PASS, pytest PASS. 1899+ tests pass.

- [ ] **Step 5: Start dev server**

Run: `task dev` (Ctrl+C to stop after confirming)
Expected: Uvicorn starts on port 8081.

Run (in another terminal): `curl -sf http://localhost:8081/healthz`
Expected: Health check responds.

- [ ] **Step 6: Verify pre-commit hook still works**

Run: `bash .claude/scripts/pre-commit`
Expected: "Pre-commit: PASS"

- [ ] **Step 7: Verify task --list shows all commands**

Run: `task --list`
Expected output (approximately):

```
task: Available tasks for this project:
* check:              Run all checks (ruff + pyright + pytest)
* check:full:         Run all checks with full test suite
* db:downgrade:       Downgrade one migration step
* db:migrate:         Run alembic migrations
* db:revision:        Create a new migration (set MSG="description")
* deps:sync:          Sync Python dependencies
* dev:                Start backend dev server (+ infrastructure)
* fmt:                Format code with ruff
* infra:              Start infrastructure services (PG, Valkey, ES)
* infra:down:         Stop infrastructure services
* infra:logs:         Tail infrastructure logs
* infra:ps:           Show running infrastructure containers
* lint:               Run ruff linter
* lint:fix:           Run ruff with auto-fix
* test:               Run tests (incremental via testmon)
* test:failed:        Re-run only last-failed tests
* test:full:          Run full test suite (no testmon)
* typecheck:          Run pyright type checker
```

- [ ] **Step 8: No commit needed — this task is verification only**

---

### Task 8: Open PR

- [ ] **Step 1: Push branch and create PR**

Run: `git push -u origin feat/dedocker-taskfile`

Create PR:
```bash
gh pr create --title "refactor: de-dockerize dev workflow + add Taskfile" --body "$(cat <<'EOF'
## Summary
- Remove `cheese_py` Docker service from dev workflow — backend runs locally via `uv run`
- Add Taskfile as unified task runner (`task check`, `task dev`, `task test`, etc.)
- Update all `.claude/` scripts and docs to use direct commands instead of `docker compose exec`
- Consolidate dev dependencies into `[dependency-groups]` so `uv sync` installs everything
- Infrastructure services (PG, Valkey, ES) remain in Docker
- Dockerfile kept for production builds

## Test plan
- [ ] `task infra` starts PG/Valkey/ES
- [ ] `task check:full` passes all checks (ruff + pyright + 1899 tests)
- [ ] `task dev` starts uvicorn on port 8081
- [ ] `task db:migrate` runs alembic successfully
- [ ] `task --list` shows all available commands
- [ ] Pre-commit hook still works
- [ ] No remaining references to `docker compose exec cheese_py` in docs
EOF
)"
```

- [ ] **Step 2: Report PR URL**
