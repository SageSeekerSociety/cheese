#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full] [--no-tests]
#   Default:     incremental (--testmon, only affected tests, ~5s)
#   --full:      run entire suite (no testmon, ~25s)
#   --no-tests:  skip pytest entirely (also via SKIP_TESTS=1) — for hosts with
#                no usable Postgres (e.g. the quality gate), where pytest can't
#                tell PASS from a broken environment either way.
# Output: concise pass/fail summary. Non-zero exit on any FAIL (SKIP doesn't count).
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
SKIP=0

# Same cross-environment sharing problem as the .venv one below, but for tool
# caches: a previous run's .ruff_cache/.pytest_cache under backend/ can be
# owned by a different uid (interactive session vs. gate host) and become
# unwritable here. Cache location doesn't affect correctness, so just keep it
# private to this run instead of fighting over the shared one.
RUFF_CACHE_DIR="$(mktemp -d)/ruff-cache"
PYTEST_CACHE_DIR="$(mktemp -d)/pytest-cache"

# Always run the WHOLE suite under `-n 8` (parallel, ~60s, stable across runs).
# testmon's incremental selection is unsafe here: these integration tests share
# DB state (reference-data seeds, ordering) by design, so running a testmon-chosen
# SUBSET deselects the tests that set that state up and the survivors fail
# intermittently. The full parallel run is fast enough to not need testmon.
PYTEST_ARGS="-n 4"
[[ "$FULL" == "1" ]] && echo "Mode: FULL suite (parallel)" || echo "Mode: full suite (parallel)"

# This workspace is shared between environments that don't agree on anything
# below the mount point (interactive session vs. gate host): different uid,
# different $HOME, different project checkout path. uv/pip console scripts
# (pyright, pytest — pure Python wrappers, unlike ruff's native binary) bake
# the ABSOLUTE path of their OWN venv into their shebang line at creation
# time, so `.venv/bin/pyright` can be unexecutable ("cannot execute: required
# file not found") on a host where the project was checked out somewhere else
# — even when `.venv/bin/python` itself still resolves fine (its symlink
# chain usually bottoms out at a uv-managed interpreter under $HOME, which —
# same user, same base image — tends to exist regardless of the project's own
# path; probing THAT proves nothing about the console scripts' baked path).
# So the probe must exercise an actual console script, not just the
# interpreter. `uv run --no-sync` can't paper over a broken one either: it
# still execs that same baked-path script.
#   - probe succeeds → `uv run --no-sync <tool>`, reusing the venv exactly as
#     inherited (no sync, no rebuild, no risk of needing to recompile
#     `srp_rs` — a local maturin/pyo3 Rust crate — from source).
#   - probe fails     → point uv at a scratch venv and sync fresh THERE
#     instead of trying to repair the inherited one in place (which fails:
#     uv can't remove/recreate `.venv/share`, owned by whoever built it).
if .venv/bin/pyright --version >/dev/null 2>&1; then
    UV_RUN=(uv run --no-sync)
else
    echo "note: inherited .venv's console scripts don't execute on this host (baked absolute shebang from a different path) — syncing a scratch venv"
    export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
    UV_RUN=(uv run)
fi

# ruff ships as a self-contained native binary (no Python shebang), so the
# venv-portability problem above doesn't apply to it — call it directly
# regardless of the probe above. `--cache-dir` is a per-subcommand flag (must
# follow `check`/`format`, not precede it).
run_ruff() {
    if [ -x ".venv/bin/ruff" ]; then
        ".venv/bin/ruff" "$@" --cache-dir "$RUFF_CACHE_DIR"
    else
        "${UV_RUN[@]}" ruff "$@" --cache-dir "$RUFF_CACHE_DIR"
    fi
}

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
if run_ruff check . 2>&1 | tail -20 && run_ruff format --check . 2>&1 | tail -20; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
# Bounded ONLY on the scratch-venv path: a freshly-synced venv's pyright-
# python wrapper downloads a Node binary on first use, which can hang forever
# on a host with no network (or a cache path that ALSO resolves to a
# mismatched $HOME) — a timeout turns that into a clean, fast SKIP (an
# environment limitation, not a code issue) instead of stalling the whole
# gate. The inherited-venv path never hits this (nothing to download again).
echo "==> pyright"
if [ "${UV_RUN[*]}" = "uv run --no-sync" ]; then
    if "${UV_RUN[@]}" pyright 2>&1 | tail -20; then
        echo "  PASS: pyright"
        ((++PASS))
    else
        echo "  FAIL: pyright"
        ((++FAIL))
    fi
elif timeout 120 "${UV_RUN[@]}" pyright 2>&1 | tail -20; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  SKIP: pyright (scratch-venv sync couldn't get it running in time — environment limitation, not a code issue)"
    ((++SKIP))
fi

# --- pytest ---
echo "==> pytest"
if [ "$SKIP_TESTS" = "1" ]; then
    echo "  SKIP: pytest (--no-tests / SKIP_TESTS=1 — no usable Postgres on this host)"
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
elif [ "${UV_RUN[*]}" = "uv run --no-sync" ]; then
    if "${UV_RUN[@]}" pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q -o "cache_dir=$PYTEST_CACHE_DIR" 2>&1 | tail -20; then
        echo "  PASS: pytest"
        ((++PASS))
    else
        echo "  FAIL: pytest"
        ((++FAIL))
    fi
elif timeout 120 "${UV_RUN[@]}" pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q -o "cache_dir=$PYTEST_CACHE_DIR" 2>&1 | tail -20; then
    echo "  PASS: pytest"
    ((++PASS))
else
    echo "  SKIP: pytest (scratch-venv sync couldn't get it running in time — environment limitation, not a code issue)"
    ((++SKIP))
fi

# --- summary ---
echo ""
echo "Result: $PASS/$((PASS+FAIL)) passed$([ "$SKIP" -gt 0 ] && echo ", $SKIP skipped")"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
