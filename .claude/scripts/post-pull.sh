#!/usr/bin/env bash
# post-pull.sh — checks to run after git pull
# Usage: bash .claude/scripts/post-pull.sh
# Git commands run on host; Python/DB commands run in Docker.
set -euo pipefail
DC="docker compose exec cheese_py sh -c"

echo "=== Post-pull checks ==="

# 1. New migrations (host)
echo ""
echo "==> New migrations"
if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -q "migrations/versions/"; then
    echo "  NEW migrations detected:"
    git diff --name-only HEAD@{1} HEAD | grep "migrations/versions/"
else
    echo "  No new migrations."
fi

# 2. Apply migrations (Docker)
echo ""
echo "==> Alembic upgrade"
$DC "cd /app && uv run alembic upgrade head"
echo "  OK: migrations up to date."

# 3. Dependency changes (host for git, Docker for sync)
echo ""
echo "==> Dependencies"
if git diff --name-only HEAD@{1} HEAD 2>/dev/null | grep -qE "pyproject.toml|uv.lock"; then
    echo "  pyproject.toml or uv.lock changed — syncing..."
    $DC "cd /app && uv sync"
    echo "  OK: dependencies synced."
else
    echo "  No dependency changes."
fi

# 4. Health check
echo ""
echo "==> Health check"
if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    echo "  OK: server responding."
else
    echo "  SKIP: server not running."
fi

echo ""
echo "=== Done ==="
