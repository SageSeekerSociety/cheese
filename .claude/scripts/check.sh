#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full] [--no-tests]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:      run entire suite (no testmon, ~25s)
#   --no-tests:  skip pytest entirely (same as SKIP_TESTS=1) — for gate hosts
#     with no usable Postgres, where a DB-backed suite can't tell PASS from FAIL.
# Output: concise pass/fail summary. Non-zero exit on any FAIL (SKIP doesn't count).
set -euo pipefail

# VCS-agnostic on purpose: this repo's version control is jj, not git, and gate
# execution environments have no .git — `git rev-parse --show-toplevel` fails there.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

SKIP_TESTS="${SKIP_TESTS:-0}"
for arg in "$@"; do
    [[ "$arg" == "--no-tests" ]] && SKIP_TESTS=1
done

PASS=0
FAIL=0
SKIP=0

# Always run the WHOLE suite under `-n 8` (parallel, ~60s, stable across runs).
# testmon's incremental selection is unsafe here: these integration tests share
# DB state (reference-data seeds, ordering) by design, so running a testmon-chosen
# SUBSET deselects the tests that set that state up and the survivors fail
# intermittently. The full parallel run is fast enough to not need testmon.
PYTEST_ARGS="-n 4"
[[ "${1:-}" == "--full" ]] && echo "Mode: FULL suite (parallel)" || echo "Mode: full suite (parallel)"

# This workspace is shared between environments that don't agree on anything
# below the mount point (interactive session vs. gate host): different uid,
# different $HOME, different uv-managed Python install location. A .venv built
# by one side has a bin/python symlink pointing into the OTHER side's $HOME,
# which doesn't exist here — `uv run --no-sync` still fails on that (not just
# on write permission). Detect a missing/foreign interpreter up front and sync
# a scratch venv instead of trying (and failing) to reuse the existing one;
# otherwise `--no-sync` reuses it as-is, skipping the sync round-trip.
VENV_PY="$(readlink -f .venv/bin/python 2>/dev/null || true)"
if [ -z "$VENV_PY" ] || [ ! -x "$VENV_PY" ]; then
    [ -d .venv ] && echo "note: .venv's interpreter is missing/foreign (built by a different environment) — syncing a scratch venv"
    export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
    UV_RUN=(uv run)
else
    UV_RUN=(uv run --no-sync)
fi

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
if "${UV_RUN[@]}" ruff check . 2>&1 | tail -1 && "${UV_RUN[@]}" ruff format --check . 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if "${UV_RUN[@]}" pyright 2>&1 | tail -2; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
echo "==> pytest"
if [ "$SKIP_TESTS" = "1" ]; then
    echo "  SKIP: pytest (--no-tests / SKIP_TESTS=1 — gate host has no usable Postgres)"
    ((++SKIP))
# Fail fast (not present, not hanging) when the test DB is unreachable. Without
# this, every DB-touching test (the large majority) fails, and with
# --reruns 2 --reruns-delay 3 each one pays a ~6s retry tax before giving up —
# across ~3000 tests that blows well past any reasonable CI/gate timeout
# instead of reporting a clear, fast "no DB" failure.
elif ! "${UV_RUN[@]}" python -c "
import socket, sys
from urllib.parse import urlparse
from app.core.config import settings

u = urlparse(settings.database_url.replace('+asyncpg', ''))
s = socket.socket()
s.settimeout(2)
try:
    s.connect((u.hostname, u.port))
except OSError as e:
    print(f'database unreachable at {u.hostname}:{u.port}: {e}', file=sys.stderr)
    sys.exit(1)
" 2>&1; then
    echo "  FAIL: pytest (no DB — skipped the run instead of paying the rerun-delay tax across ~3000 tests)"
    ((++FAIL))
elif "${UV_RUN[@]}" pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -3; then
    echo "  PASS: pytest"
    ((++PASS))
else
    echo "  FAIL: pytest"
    ((++FAIL))
fi

# --- summary ---
echo ""
echo "Result: $PASS/$((PASS+FAIL)) passed$([ "$SKIP" -gt 0 ] && echo ", $SKIP skipped")"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
