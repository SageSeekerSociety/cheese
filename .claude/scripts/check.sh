#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel)
# Usage: bash .claude/scripts/check.sh [--full] [--no-tests]
#   --full:      run entire suite (default behaviour today)
#   --no-tests:  lint + typecheck only, skip pytest (what the 机器闸门 passes)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

# Locate the repo from THIS script's own path, not from git: a topic's工作区 is a
# jj workspace with no `.git`, so `git rev-parse --show-toplevel` exits 128 and
# `set -e` kills the run before a single check executes.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

PASS=0
FAIL=0

# Always run the WHOLE suite under `-n 8` (parallel, ~60s, stable across runs).
# testmon's incremental selection is unsafe here: these integration tests share
# DB state (reference-data seeds, ordering) by design, so running a testmon-chosen
# SUBSET deselects the tests that set that state up and the survivors fail
# intermittently. The full parallel run is fast enough to not need testmon.
PYTEST_ARGS="-n 4"
NO_TESTS=0
for arg in "$@"; do [[ "$arg" == "--no-tests" ]] && NO_TESTS=1; done

# The 机器闸门 runs this inside a NETWORK-LESS container (cheesex-gate-*), so any
# `uv run` that tries to resolve/sync the environment dies on DNS before a single
# check executes. `--no-sync` makes uv use the workspace's existing .venv as-is.
UV=""
for candidate in "uv run --no-sync" "uv run --offline" "uv run"; do
    if $candidate python -c "" >/dev/null 2>&1; then UV="$candidate"; break; fi
done
if [ -z "$UV" ]; then
    echo "  FAIL: no usable python environment (tried --no-sync, --offline, and a syncing uv)"
    echo "  hint: the 闸门 container has no network; the workspace .venv must be usable as-is"
    exit 1
fi
[[ "$UV" == "uv run --no-sync" ]] || echo "note: using \`$UV\` (the workspace .venv was not directly usable)"

[[ "$NO_TESTS" == 1 ]] && echo "Mode: lint + typecheck only (--no-tests)" \
                       || echo "Mode: full suite (parallel)"

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
if $UV ruff check . 2>&1 | tail -1 && $UV ruff format --check . 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if $UV pyright 2>&1 | tail -2; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
if [[ "$NO_TESTS" == 1 ]]; then
    echo "==> pytest (skipped: --no-tests)"
else
    echo "==> pytest"
    if $UV pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -3; then
        echo "  PASS: pytest"
        ((++PASS))
    else
        echo "  FAIL: pytest"
        ((++FAIL))
    fi
fi

# --- summary ---
echo ""
echo "Result: $PASS/$((PASS+FAIL)) passed"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
