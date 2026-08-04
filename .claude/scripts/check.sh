#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full]
#   Default: incremental (--testmon, only affected tests, ~5s)
#   --full:  run entire suite (no testmon, ~25s)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

# VCS-agnostic on purpose: this repo's version control is jj, not git, and gate
# execution environments have no .git — `git rev-parse --show-toplevel` fails there.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

# Shared workspace, not always the same uid across runs (interactive session vs.
# gate). A stale .venv left by a different user can't be rebuilt/cleaned by uv
# (Permission denied) — detect that and build the venv somewhere scratch instead.
if [ -d .venv/share ] && ! [ -w .venv/share ]; then
    echo "note: .venv/share isn't writable (stale venv from another user) — using a scratch venv"
    export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
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
# Fail fast (not present, not hanging) when the test DB is unreachable. Without
# this, every DB-touching test (the large majority) fails, and with
# --reruns 2 --reruns-delay 3 each one pays a ~6s retry tax before giving up —
# across ~3000 tests that blows well past any reasonable CI/gate timeout
# instead of reporting a clear, fast "no DB" failure.
if ! uv run python -c "
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
elif uv run pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -3; then
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
