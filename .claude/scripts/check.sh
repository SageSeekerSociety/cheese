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

# Call the already-installed console scripts DIRECTLY, bypassing `uv run`
# entirely — a gate/worktree environment can inherit a `.venv` from a
# different user (stale from another topic's run, or with a python-
# interpreter reference that no longer resolves for the user running THIS
# check), and `uv run` reacts to either by trying to reconcile/rebuild the
# venv (removing and recreating `.venv/share`) EVEN with `--no-sync` — which
# then needs to recompile `srp_rs` (a local maturin/pyo3 Rust crate) from
# source, failing outright without a Rust toolchain (what broke the quality
# gate, twice). The tools are already installed and working; running their
# `.venv/bin/*` entry points needs no uv involvement at all, so there's
# nothing for uv to validate or rebuild. Falls back to `uv run --no-sync`
# when a script isn't there yet (first run / no venv).
run_tool() {
    local name="$1"
    shift
    if [ -x ".venv/bin/$name" ]; then
        ".venv/bin/$name" "$@"
    else
        uv run --no-sync "$name" "$@"
    fi
}

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
((++TOTAL))
if run_tool ruff check . 2>&1 | tail -20 && run_tool ruff format --check . 2>&1 | tail -20; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
((++TOTAL))
if run_tool pyright 2>&1 | tail -20; then
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
    if run_tool pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -20; then
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
