#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (all in Docker)
# Usage: bash .claude/scripts/check.sh
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

PASS=0
FAIL=0
DC="docker compose exec cheese_py sh -c"

# --- ruff ---
echo "==> ruff check"
if $DC "cd /app && uv run ruff check ." 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if $DC "cd /app && uv run pyright" 2>&1 | tail -2; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
echo "==> pytest"
if $DC "cd /app && uv run python -m pytest tests/ -q" 2>&1 | tail -3; then
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
