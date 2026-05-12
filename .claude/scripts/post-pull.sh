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
    uv sync
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
