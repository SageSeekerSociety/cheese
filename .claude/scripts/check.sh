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
# host/user (e.g. built under /home/node, then copied into a worktree under
# /home/nictheboy/...): `uv`/pip console scripts bake an ABSOLUTE shebang path
# to their own venv's interpreter at creation time, so `.venv/bin/pyright` /
# `.venv/bin/pytest` (pure-Python wrapper scripts) can become unexecutable
# once relocated ("cannot execute: required file not found"). `uv run` can't
# help either: even with `--no-sync`, it still validates the venv's OWN
# interpreter reference first and, finding it broken, tries to repair the
# venv in place (removing/recreating `.venv/share`) before running anything —
# which then needs to recompile `srp_rs` (a local maturin/pyo3 Rust crate)
# from source, failing without a Rust toolchain (what broke the gate before).
#
# Tried and REJECTED: running these as `python3 -m <tool>` via the system
# interpreter with PYTHONPATH pointed at the venv's site-packages sidesteps
# the broken shebang/interpreter entirely and gave correct results twice in a
# row locally — but a third identical invocation hung indefinitely (2+ min,
# no output) instead of failing fast. An intermittent hang on the quality gate
# is worse than a clean failure, so this script does NOT use that trick.
#
# What's actually applied: call the installed `.venv/bin/*` entry point
# directly (works whenever the venv wasn't relocated — the common case, and
# what's used for local dev) and fall back to `uv run --no-sync` only when
# there's no venv yet at all (first run). If the venv WAS relocated, this
# fails fast and deterministically (a plain exec error) — better than a hang,
# and diagnosable from the (now untruncated) output.
run_tool() {
    local name="$1"
    shift
    if [ -x ".venv/bin/$name" ]; then
        ".venv/bin/$name" "$@"
    else
        uv run --no-sync "$name" "$@"
    fi
}

# ruff's on-disk cache dir can be the SAME kind of cross-user leftover as
# `.venv/share` (`.ruff_cache/<version>/...` owned by whoever ran it last) —
# point it at a fresh scratch dir instead of trying to detect/repair the
# inherited one. `--cache-dir` is a per-subcommand flag (must follow
# `check`/`format`, not precede it).
RUFF_CACHE_DIR="$(mktemp -d)"

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
((++TOTAL))
if run_tool ruff check --cache-dir "$RUFF_CACHE_DIR" . 2>&1 | tail -20 \
    && run_tool ruff format --cache-dir "$RUFF_CACHE_DIR" --check . 2>&1 | tail -20; then
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
