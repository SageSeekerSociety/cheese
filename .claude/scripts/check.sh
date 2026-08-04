#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:  run entire suite (no testmon, ~25s)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

# Resolve the repo root from this script's own path, not `git rev-parse` — this
# repo's VCS is jj, and the quality-gate execution environment has no .git, so a
# git-based lookup fails fatally before any check even runs.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

# A gate/worktree environment can inherit a `.venv` created by a different user
# than the one running this check (stale from another topic's run) — uv can't
# clean/rebuild `.venv/share` in that case and dies. Prefer removing just that
# stale subtree in place (owning `.venv` lets us unlink it even if we don't own
# every file inside, and the rest of the venv — including the already-built
# `srp_rs` Rust extension — stays put, so uv only reinstalls the small bits of
# `share/`). If even that's blocked (the WHOLE `.venv` is inherited read-only,
# not just `share/`), do NOT fall back to a scratch venv: that forces uv to
# rebuild EVERY dependency from source, including `srp_rs` (a local
# maturin/pyo3 crate) — which fails outright without a Rust toolchain (seen on
# the quality gate). Instead, tell uv to skip syncing entirely and run against
# the inherited venv exactly as-is: it already has everything built (including
# srp_rs), so nothing needs to be writable for a read-only run.
if [ -d .venv/share ] && ! [ -w .venv/share ]; then
    echo "note: .venv/share isn't writable (stale venv from another user)"
    if rm -rf .venv/share 2>/dev/null; then
        echo "note: removed the stale .venv/share in place; uv will recreate it"
    else
        echo "note: can't modify the inherited .venv at all — skipping uv's sync" \
             "step (UV_NO_SYNC) and running against it read-only instead of" \
             "rebuilding (a rebuild would need a Rust toolchain for srp_rs)"
        export UV_NO_SYNC=1
    fi
fi

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
if uv run ruff check . 2>&1 | tail -1 && uv run ruff format --check . 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
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
if uv run pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -3; then
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
