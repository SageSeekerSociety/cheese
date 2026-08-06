#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel)
# Usage: bash .claude/scripts/check.sh [--full] [--no-tests]
#   --full:      run entire suite (default behaviour today)
#   --no-tests:  lint + typecheck only, skip pytest (what the 机器闸门 passes)
# Output: concise pass/fail summary. Non-zero exit on failure.
set -euo pipefail

# Locate the repo from THIS script's own path, not from git: a topic's工作区 is a
# jj workspace with no `.git`, so `git rev-parse --show-toplevel` exits 128 and
# `set -e` kills the run before a single check executes.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT/backend"

PASS=0
FAIL=0

# Always run the WHOLE suite under `-n 8` (parallel, ~60s, stable across runs).
# testmon's incremental selection is unsafe here: these integration tests share
# DB state (reference-data seeds, ordering) by design, so running a testmon-chosen
# SUBSET deselects the tests that set that state up and the survivors fail
# intermittently. The full parallel run is fast enough to not need testmon.
PYTEST_ARGS="-n 4"
NO_TESTS=0
for arg in "$@"; do [[ "$arg" == "--no-tests" ]] && NO_TESTS=1; done

# The 机器闸门 runs this inside a NETWORK-LESS container (cheesex-gate-*), so any
# `uv run` that tries to resolve/sync the environment dies on DNS before a single
# check executes. `--no-sync` makes uv use the workspace's existing .venv as-is.
UV=""
for candidate in "uv run --no-sync" "uv run --offline" "uv run"; do
    if $candidate python -c "" >/dev/null 2>&1; then UV="$candidate"; break; fi
done

# No uv environment (the 闸门 container has no network AND the workspace .venv is
# not relocatable — its interpreter symlink points into the authoring container).
# Fall back to invoking the tools directly: ruff is a standalone Rust binary and
# needs no Python at all, so lint/format still runs. Dump what we found either
# way — a gate run is an expensive round trip, so it must be self-diagnosing.
if [ -z "$UV" ]; then
    echo "note: no usable uv environment — falling back to direct .venv binaries"
    echo "--- environment probe (for diagnosing the gate container) ---"
    echo "  pwd=$(pwd)"
    echo "  uv: $(command -v uv || echo MISSING) $(uv --version 2>/dev/null || true)"
    echo "  system python3: $(command -v python3 || echo MISSING) $(python3 -V 2>&1 || true)"
    echo "  node: $(command -v node || echo MISSING) $(node -v 2>/dev/null || true)"
    echo "  .venv/bin: $(ls .venv/bin 2>/dev/null | tr '\n' ' ' | cut -c1-200)"
    echo "  pyvenv.cfg home: $(grep -m1 '^home' .venv/pyvenv.cfg 2>/dev/null || echo NONE)"
    echo "  uv cache: $(ls -d "${UV_CACHE_DIR:-$HOME/.cache/uv}" 2>/dev/null || echo MISSING)"
    echo "  dns: $(getent hosts pypi.org >/dev/null 2>&1 && echo ok || echo unavailable)"
    echo "-------------------------------------------------------------"
fi

# Resolve one runner per tool. A venv script's exec bit says nothing about whether
# it RUNS: .venv/bin/pyright is a Python wrapper whose shebang points at the
# authoring container's interpreter, so it is -x yet "cannot execute". Probe each
# candidate for real. A tool we cannot run must fail alone, never take down the
# others.
tool() {
    local name="$1"
    if [ -n "$UV" ] && $UV "$name" --version >/dev/null 2>&1; then echo "$UV $name"; return; fi
    if ".venv/bin/$name" --version >/dev/null 2>&1; then echo ".venv/bin/$name"; return; fi
    echo ""
}
RUFF="$(tool ruff)"; PYRIGHT="$(tool pyright)"; PYTEST="$(tool pytest)"

# pyright is a NODE program; the Python package only wraps it and ships the real
# bundle inside site-packages. When no Python works but node does, run the bundle
# directly — that is the genuine type check, not a skipped one.
if [ -z "$PYRIGHT" ] && command -v node >/dev/null 2>&1; then
    PJS="$(ls .venv/lib/python*/site-packages/pyright/dist/index.js 2>/dev/null | head -1)"
    if [ -n "$PJS" ] && node "$PJS" --version >/dev/null 2>&1; then
        PYRIGHT="node $PJS"
        echo "note: pyright via bundled node entrypoint ($PJS)"
    fi
fi

[[ -n "$UV" && "$UV" == "uv run --no-sync" ]] || echo "note: runner = ruff:[${RUFF:-none}] pyright:[${PYRIGHT:-none}] pytest:[${PYTEST:-none}]"

[[ "$NO_TESTS" == 1 ]] && echo "Mode: lint + typecheck only (--no-tests)" \
                       || echo "Mode: full suite (parallel)"

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
if [ -z "$RUFF" ]; then echo "  FAIL: ruff unavailable"; ((++FAIL));
elif $RUFF check . 2>&1 | tail -1 && $RUFF format --check . 2>&1 | tail -1; then
    echo "  PASS: ruff"
    ((++PASS))
else
    echo "  FAIL: ruff (lint or format)"
    ((++FAIL))
fi

# --- pyright ---
echo "==> pyright"
if [ -z "$PYRIGHT" ]; then echo "  FAIL: pyright unavailable"; ((++FAIL));
elif $PYRIGHT 2>&1 | tail -2; then
    echo "  PASS: pyright"
    ((++PASS))
else
    echo "  FAIL: pyright"
    ((++FAIL))
fi

# --- pytest ---
if [[ "$NO_TESTS" == 1 ]]; then
    echo "==> pytest (skipped: --no-tests)"
else
    echo "==> pytest"
    if [ -z "$PYTEST" ]; then echo "  FAIL: pytest unavailable"; ((++FAIL));
    elif $PYTEST tests/ $PYTEST_ARGS --reruns 2 --reruns-delay 3 -q 2>&1 | tail -3; then
        echo "  PASS: pytest"
        ((++PASS))
    else
        echo "  FAIL: pytest"
        ((++FAIL))
    fi
fi

# --- summary ---
echo ""
echo "Result: $PASS/$((PASS+FAIL)) passed"
if [ "$FAIL" -gt 0 ]; then exit 1; fi
