#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full] [--no-tests]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:      run entire suite (no testmon, ~25s)
#   --no-tests:  skip pytest entirely (also via SKIP_TESTS=1) — for hosts with
#                no usable Postgres (e.g. the quality gate), where pytest can't
#                tell PASS from a broken environment either way.
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

# Resolve the repo root from this script's own path, not `git rev-parse` — this
# repo's VCS is jj, and the quality-gate execution environment has no .git, so a
# git-based lookup fails fatally before any check even runs.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

SKIP_TESTS="${SKIP_TESTS:-0}"
FULL=0
for arg in "$@"; do
    case "$arg" in
        --full) FULL=1 ;;
        --no-tests) SKIP_TESTS=1 ;;
    esac
done

PASS=0
FAIL=0
TOTAL=0

# Always run the WHOLE suite under `-n 8` (parallel, ~60s, stable across runs).
# testmon's incremental selection is unsafe here: these integration tests share
# DB state (reference-data seeds, ordering) by design, so running a testmon-chosen
# SUBSET deselects the tests that set that state up and the survivors fail
# intermittently. The full parallel run is fast enough to not need testmon.
PYTEST_ARGS="-n 4"
[[ "$FULL" == "1" ]] && echo "Mode: FULL suite (parallel)" || echo "Mode: full suite (parallel)"

# `--no-sync`: never let a check step try to reconcile/rebuild `.venv` — a
# gate/worktree environment can inherit one created by a different user (stale
# from another topic's run, or with a python-interpreter symlink that no
# longer resolves), and uv reacts to either by trying to rebuild it. That
# rebuild needs to recompile `srp_rs` (a local maturin/pyo3 Rust crate) from
# source, which fails outright without a Rust toolchain — exactly what broke
# the quality gate. `--no-sync` runs against whatever's already installed
# (srp_rs included) and never touches the venv, sidestepping all of that.

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
((++TOTAL))
if uv run --no-sync ruff check . 2>&1 | tail -1 && uv run --no-sync ruff format --check . 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
((++TOTAL))
if uv run --no-sync pyright 2>&1 | tail -2; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
echo "==> pytest"
if [ "$SKIP_TESTS" = "1" ]; then
    echo "  SKIP: pytest (--no-tests / SKIP_TESTS=1 — no usable Postgres on this host)"
else
    ((++TOTAL))
    if uv run --no-sync pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -3; then
        echo "  PASS: pytest"
        ((++PASS))
    else
        echo "  FAIL: pytest"
        ((++FAIL))
    fi
fi

# --- summary ---
echo ""
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
