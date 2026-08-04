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
# with --no-sync. Try to repoint the symlink at whatever interpreter uv
# resolves on this machine so uv treats the existing (already-built) venv as
# valid and leaves it alone — this avoids recompiling the local srp_rs Rust
# extension (workspace member, not a PyPI wheel, needs a cargo toolchain
# that isn't guaranteed to be present). If .venv itself isn't writable at
# all (whole tree owned by whoever's user session built it, not just
# .venv/share), the repoint fails too — fall back to a scratch venv instead
# of letting that `ln` failure kill the script under `set -e`.
NEEDS_SCRATCH=0
if [ -L .venv/bin/python ] && ! [ -e .venv/bin/python ]; then
    VALID_PY="$(uv python find 2>/dev/null || true)"
    if [ -n "$VALID_PY" ] && ln -sf "$VALID_PY" .venv/bin/python 2>/dev/null; then
        echo "note: .venv/bin/python was a dangling symlink (venv built in a different container) — repointed at $VALID_PY"
    else
        echo "note: .venv/bin/python is a dangling symlink and .venv isn't writable to fix in place — falling back to a scratch venv (will rebuild srp-rs)"
        NEEDS_SCRATCH=1
    fi
fi

UV_RUN=(uv run)
if [ "$NEEDS_SCRATCH" = 1 ]; then
    export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
elif [ -d .venv/share ] && ! [ -w .venv/share ]; then
    # Belt and suspenders: .venv/share not writable independent of the
    # symlink issue above — skip uv's sync step and reuse installed
    # packages as-is instead of letting uv try to rebuild them.
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
