#!/usr/bin/env bash
# check-alembic-head.sh — exactly one migration head, and the HEAD sentinel names it.
#
# Parallel migrations fork the chain; `alembic upgrade head` then refuses and the
# deploy aborts — that happened four times on 2026-08-09/10. The sentinel file
# (backend/alembic/HEAD, one line) exists so two concurrent migration PRs collide
# in GIT, on that line, instead of merging cleanly into a silent fork: GitHub
# re-evaluates a conflict continuously as main moves, while a green check goes
# stale the moment a sibling merges.
#
# Reads the migration graph only — no database. Extracted from check.sh when the
# checks moved to pre-commit, so that "one rule, one executable, exit code is the
# interface" holds for every guard rather than most of them.
#
# Exit: 0 pass, 1 the tree is wrong, 2 alembic could not run here (an
# environment limit — the caller decides whether that blocks anything).
#
# Usage: check-alembic-head.sh [backend-dir]   (default: ../../backend)
#        check-alembic-head.sh --self-test
set -euo pipefail

if [ "${1:-}" = "--self-test" ]; then
    # The parsing is what breaks, not the alembic call: `grep -c '(head)'` and
    # the awk that lifts the revision id both depend on alembic's output shape.
    sample=$'a1b2c3d4e5f6 (head)\n'
    n="$(printf '%s' "$sample" | grep -c '(head)')"
    [ "$n" = 1 ] || { echo "SELF-TEST FAIL: single head not counted as 1"; exit 1; }
    tip="$(printf '%s' "$sample" | awk '/\(head\)/ {print $1; exit}')"
    [ "$tip" = "a1b2c3d4e5f6" ] || { echo "SELF-TEST FAIL: tip parsed as '$tip'"; exit 1; }
    two=$'aaa (head)\nbbb (head)\n'
    [ "$(printf '%s' "$two" | grep -c '(head)')" = 2 ] || {
        echo "SELF-TEST FAIL: two heads not counted as 2"; exit 1; }
    echo "PASS: check-alembic-head self-test (head counting and tip parsing)"
    exit 0
fi

BACKEND="${1:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../backend" && pwd)}"
cd "$BACKEND"

if ! HEADS_OUT="$(timeout 60 uv run alembic heads 2>/dev/null)"; then
    echo "BLOCKED: could not run alembic here (environment, not a code verdict)"
    exit 2
fi

HEADS_N="$(printf '%s\n' "$HEADS_OUT" | grep -c '(head)')"
if [ "$HEADS_N" != "1" ]; then
    printf '%s\n' "$HEADS_OUT" | sed 's/^/    /'
    echo "FAIL: $HEADS_N migration heads — rechain your migration onto the current"
    echo "      head (see .claude/rules/migrations.md)"
    exit 1
fi

TIP="$(printf '%s\n' "$HEADS_OUT" | awk '/\(head\)/ {print $1; exit}')"
# Test for the file rather than redirecting from it and hoping: a failed `<`
# redirection is the SHELL's error, so bash prints "alembic/HEAD: No such file
# or directory" regardless of any 2>/dev/null on the command, and that raw line
# lands above the written-for-humans message below.
SENTINEL=""
[ -f alembic/HEAD ] && SENTINEL="$(tr -d '[:space:]' < alembic/HEAD)"

if [ "$SENTINEL" != "$TIP" ]; then
    echo "FAIL: backend/alembic/HEAD says '${SENTINEL:-<missing>}' but the tip is $TIP"
    echo "      every migration moves this one-line file:"
    echo "      echo $TIP > backend/alembic/HEAD"
    exit 1
fi

echo "PASS: exactly one migration head, HEAD sentinel matches ($TIP)"
