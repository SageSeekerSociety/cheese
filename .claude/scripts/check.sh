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

# A stale .venv left behind by a different user in this worktree can't be
# removed/rebuilt by uv (Permission denied on .venv/share) — reuse it as-is
# via --no-sync instead of letting `uv run` try to recreate it. Rebuilding
# from scratch would also require recompiling the local srp_rs Rust
# extension (workspace member, not a PyPI wheel), which needs a cargo
# toolchain that isn't guaranteed to be present here — reusing the already
# -built venv sidesteps that entirely.
UV_RUN=(uv run)
if [ -d .venv/share ] && ! [ -w .venv/share ]; then
    echo "note: .venv/share isnt writable (stale venv from another user) — reusing it as-is via --no-sync"
    UV_RUN=(uv run --no-sync)
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
if "${UV_RUN[@]}" ruff check . 2>&1 | tail -20 && "${UV_RUN[@]}" ruff format --check . 2>&1 | tail -20; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if "${UV_RUN[@]}" pyright 2>&1 | tail -20; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
echo "==> pytest"
if "${UV_RUN[@]}" pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -20; then
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
