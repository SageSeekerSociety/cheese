#!/usr/bin/env bash
# post-pull.sh — checks to run after git pull or merge
# Usage: bash .claude/scripts/post-pull.sh
# POST_PULL_UV / POST_PULL_PNPM override executable paths (not shell commands).
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
if ! git rev-parse --verify 'ORIG_HEAD^{commit}' >/dev/null 2>&1; then
    echo "ERROR: ORIG_HEAD unavailable; run after git pull or merge to compare changes." >&2
    exit 2
fi
CHANGED_FILES="$(git diff --name-only --no-renames ORIG_HEAD HEAD --)"
UV="${POST_PULL_UV:-uv}"
PNPM="${POST_PULL_PNPM:-pnpm}"
cd "$REPO_ROOT/backend"

changed() {
    grep -Fx -- "$1" <<< "$CHANGED_FILES" >/dev/null
}

echo "=== Post-pull checks ==="

# 1. New migrations
echo ""
echo "==> New migrations"
if grep -E '^backend/alembic/versions/' <<< "$CHANGED_FILES"; then
    echo "  NEW migrations detected above."
else
    echo "  No new migrations."
fi

# 2. Apply migrations
echo ""
echo "==> Alembic upgrade"
"$UV" run alembic upgrade head
echo "  OK: migrations up to date."

# 3. Dependency changes
echo ""
echo "==> Dependencies"
if changed backend/pyproject.toml || changed backend/uv.lock; then
    echo "  backend/pyproject.toml or backend/uv.lock changed — syncing..."
    "$UV" sync
    echo "  OK: backend dependencies synced."
else
    echo "  No backend dependency changes."
fi
if changed frontend/pnpm-lock.yaml; then
    echo "  frontend/pnpm-lock.yaml changed — installing..."
    (cd "$REPO_ROOT" && "$PNPM" --dir frontend install --frozen-lockfile)
    echo "  OK: frontend dependencies synced."
else
    echo "  No frontend dependency changes."
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
