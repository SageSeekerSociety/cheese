#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:  run entire suite (no testmon, ~25s)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
cd "$REPO_ROOT/backend"

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
