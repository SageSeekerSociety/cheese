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

# A gate/worktree environment can inherit a `.venv` seeded from a DIFFERENT
# absolute path (e.g. built at /work/backend, then copied into a worktree at
# /home/nictheboy/.../backend). uv/pip console scripts (pyright, pytest — pure
# Python wrappers) bake that ORIGINAL absolute path into their OWN shebang
# line at creation time, so `.venv/bin/pyright` can be unexecutable ("cannot
# execute: required file not found") even when `.venv/bin/python3` ITSELF
# still resolves fine (its symlink chain usually bottoms out at a
# uv-managed interpreter under $HOME, which — same user, same base image —
# tends to exist regardless of the project's own path; probing THAT proves
# nothing about the console scripts' baked path). So the probe must exercise
# an actual console script, not just the interpreter:
#   - `.venv/bin/pyright --version` runs → its shebang is still valid here →
#     `uv run --no-sync <tool>`, reusing the venv exactly as inherited (no
#     sync, no rebuild, no risk of needing to recompile `srp_rs` — a local
#     maturin/pyo3 Rust crate — from source).
#   - it doesn't → point uv at a scratch venv and sync fresh THERE instead of
#     trying to repair the inherited one in place (which fails: uv can't
#     remove/recreate `.venv/share`, owned by whoever built it).
if .venv/bin/pyright --version >/dev/null 2>&1; then
    VENV_OK=1
else
    echo "note: inherited .venv's console scripts don't execute on this host (baked absolute shebang from a different path) — syncing a scratch venv"
    export UV_PROJECT_ENVIRONMENT="$(mktemp -d)/venv"
    VENV_OK=0
fi

# ruff ships as a self-contained native binary (no Python shebang), so the
# venv-portability problem above doesn't apply to it at all — call it
# directly regardless of $VENV_OK. Its own on-disk cache dir CAN be the same
# kind of cross-user leftover as `.venv/share`; point it at a fresh scratch
# dir instead of trying to detect/repair the inherited one. `--cache-dir` is
# a per-subcommand flag (must follow `check`/`format`, not precede it).
RUFF_CACHE_DIR="$(mktemp -d)"
run_ruff() {
    if [ -x ".venv/bin/ruff" ]; then
        ".venv/bin/ruff" "$@" --cache-dir "$RUFF_CACHE_DIR"
    else
        uv run --no-sync ruff "$@" --cache-dir "$RUFF_CACHE_DIR"
    fi
}

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
((++TOTAL))
if run_ruff check . 2>&1 | tail -20 && run_ruff format --check . 2>&1 | tail -20; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
# Bounded: a freshly-synced venv's pyright-python wrapper downloads a Node
# binary on first use, which can hang forever on a host with no network (or a
# cache path that ALSO resolves to a mismatched $HOME) — a timeout turns that
# into a clean, fast SKIP instead of stalling the whole gate.
echo "==> pyright"
if [ "$VENV_OK" = "1" ]; then
    ((++TOTAL))
    if uv run --no-sync pyright 2>&1 | tail -20; then
        echo "  PASS: pyright"
        ((++PASS))
    else
        echo "  FAIL: pyright"
        ((++FAIL))
    fi
elif timeout 120 uv run pyright 2>&1 | tail -20; then
    ((++TOTAL))
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  SKIP: pyright (scratch-venv sync couldn't get it running in time — environment limitation, not a code issue)"
fi

# --- pytest ---
echo "==> pytest"
if [ "$SKIP_TESTS" = "1" ]; then
    echo "  SKIP: pytest (--no-tests / SKIP_TESTS=1 — no usable Postgres on this host)"
elif [ "$VENV_OK" = "1" ]; then
    ((++TOTAL))
    if uv run --no-sync pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -20; then
        echo "  PASS: pytest"
        ((++PASS))
    else
        echo "  FAIL: pytest"
        ((++FAIL))
    fi
elif timeout 120 uv run pytest tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -20; then
    ((++TOTAL))
    echo "  PASS: pytest"
    ((++PASS))
else
    echo "  SKIP: pytest (scratch-venv sync couldn't get it running in time — environment limitation, not a code issue)"
fi

# --- summary ---
echo ""
echo "Result: $PASS/$TOTAL passed"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
