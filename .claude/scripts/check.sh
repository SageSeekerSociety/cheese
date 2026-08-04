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

# A .venv built in a different container has a dangling bin/python symlink
# (target interpreter no longer exists) — uv treats this as invalid and
# unconditionally wipes + rebuilds the whole venv on EVERY `uv run`, even
# with --no-sync. That rebuild would also recompile the local srp_rs Rust
# extension (workspace member, not a PyPI wheel), which needs a cargo
# toolchain that isn't guaranteed to be present here. Repoint the symlink at
# whatever interpreter uv resolves on this machine so it treats the existing
# (already-built) venv as valid and leaves it alone.
if [ -L .venv/bin/python ] && ! [ -e .venv/bin/python ]; then
    VALID_PY="$(uv python find 2>/dev/null || true)"
    if [ -n "$VALID_PY" ]; then
        echo "note: .venv/bin/python is a dangling symlink (venv built in a different container) — repointing at $VALID_PY"
        ln -sf "$VALID_PY" .venv/bin/python
    fi
fi

# Belt and suspenders: if .venv/share is still owned by a different user and
# not writable (independent of the symlink issue above), skip uv's sync step
# entirely and reuse the venv's already-installed packages as-is.
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
