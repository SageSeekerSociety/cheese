#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:  run entire suite (no testmon, ~25s)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

# Resolve by script location, not `git rev-parse` — this repo's VCS is jj and
# the gate execution sandbox has no .git, so a git-based lookup fails at step 1.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

# A .venv built in a different container/worktree can be stale in ways that
# vary run to run (dangling interpreter symlink, unwritable share/, unwritable
# bin/, ...) — trying to predict and pre-fix each shape has repeatedly guessed
# wrong. Instead: try the normal (fast, reuses the existing .venv) path, and
# only if uv actually fails trying to touch it, retry once against a fresh
# scratch venv+cargo-target-dir we know we own. This reacts to whatever's
# actually broken instead of guessing at it ahead of time.
run_uv() {
    local out
    if out="$(uv run "$@" 2>&1)"; then
        printf '%s\n' "$out" | tail -20
        return 0
    fi
    if [ -z "${UV_PROJECT_ENVIRONMENT:-}" ] && printf '%s\n' "$out" | grep -qi "permission denied"; then
        echo "note: existing .venv isn't writable in this worktree — retrying once in a scratch venv"
        export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
        export CARGO_TARGET_DIR="$(mktemp -d)/cargo-target"
        if out="$(uv run "$@" 2>&1)"; then
            printf '%s\n' "$out" | tail -20
            return 0
        fi
    fi
    printf '%s\n' "$out" | tail -20
    return 1
}

PASS=0
FAIL=0

# Always run the WHOLE suite under `-n 8` (parallel, ~60s, stable across runs).
# testmon's incremental selection is unsafe here: these integration tests share
# DB state (reference-data seeds, ordering) by design, so running a testmon-chosen
# SUBSET deselects the tests that set that state up and the survivors fail
# intermittently. The full parallel run is fast enough to not need testmon.
PYTEST_ARGS="-n 4"
[[ "${1:-}" == "--full" ]] && echo "Mode: FULL suite (parallel)" || echo "Mode: full suite (parallel)"

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
if run_uv ruff check . && run_uv ruff format --check .; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if run_uv pyright; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
echo "==> pytest"
if run_uv pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q; then
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
