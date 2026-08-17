#!/usr/bin/env bash
# check.sh — ruff + pyright + pytest (parallel + incremental)
# Usage: bash .claude/scripts/check.sh [--full] [--no-tests] [--strict] [--self-test]
#   Default:     incremental (--testmon, only affected tests, ~5s)
#   --full:      run entire suite (no testmon, ~25s)
#   --no-tests:  skip pytest entirely (also via SKIP_TESTS=1) — for hosts with
#                no usable Postgres (e.g. the quality gate), where pytest can't
#                tell PASS from a broken environment either way.
#   --strict:    (also via CHECK_STRICT=1) a check that couldn't RUN is not a
#                pass — see the three outcomes below. The platform quality gate
#                sets this; local runs don't.
#   --self-test: exercise the pass/fail/blocked accounting itself and exit.
#                Runs no checks and needs no toolchain (same convention as the
#                sibling guards in this directory).
#
# Three outcomes per check, deliberately distinct:
#   PASS/FAIL  the check ran and had an opinion about the code.
#   BLOCKED    the check could NOT run here (toolchain missing/unreachable).
#              Not a code verdict — but not a pass either.
#   SKIP       we asked for it to be skipped (--no-tests). Intentional.
#
# Exit: 1 on any FAIL. 2 when --strict and anything is BLOCKED (= "the check
# never ran", which a gate must not paint green). 0 otherwise — so a plain local
# run on a machine without the toolchain still gets out of a developer's way.
set -euo pipefail

# Resolve the repo root from this script's own path, not `git rev-parse` — this
# repo's VCS is jj, and the quality-gate execution environment has no .git, so a
# git-based lookup fails fatally before any check even runs.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

SKIP_TESTS="${SKIP_TESTS:-0}"
STRICT="${CHECK_STRICT:-0}"
FULL=0
SELF_TEST=0
for arg in "$@"; do
    case "$arg" in
        --full) FULL=1 ;;
        --no-tests) SKIP_TESTS=1 ;;
        --strict) STRICT=1 ;;
        --self-test) SELF_TEST=1 ;;
    esac
done

PASS=0
FAIL=0
SKIP=0
BLOCKED=0

# --- what this change actually touches -------------------------------------
# Without this every commit paid for every check: a docs-only edit ran ~60s of
# pytest (and failed outright on a machine with no Postgres), while a
# frontend-only edit ran that same pytest and got ZERO frontend checks, because
# everything below cds into backend/. Both halves of that are fixed here.
#
# `scope_of` is a pure function over a file list so --self-test can pin it on a
# host with no repo at all. The rule is deliberately asymmetric: a path is
# treated as backend-affecting unless it is KNOWN not to be. Getting that
# backwards would silently skip the suite on a path nobody classified yet, and a
# check that quietly does not run is the failure mode this whole script exists
# to prevent.
scope_of() {
  local backend=0 frontend=0 path
  for path in "$@"; do
    case "$path" in
      frontend/*) frontend=1 ;;
      # Neither: prose and CI config cannot change backend behaviour. e2e/ is
      # its own CI job and never imports the backend package.
      docs/*|*.md|.github/*|e2e/*|.claude/rules/*|.claude/skills/*) ;;
      # .claude/scripts/* IS backend-affecting: the guards run against
      # backend/app, and their own tests live in backend/tests.
      *) backend=1 ;;
    esac
  done
  # An empty change set means we could not tell — run everything.
  [ "$#" -eq 0 ] && { echo "backend frontend"; return; }
  local out=""
  [ "$backend" = 1 ] && out="backend"
  [ "$frontend" = 1 ] && out="${out:+$out }frontend"
  echo "${out:-none}"
}

# Uncommitted work plus whatever is staged. Not a diff against main: this script
# answers "is what I have here sound", and a rebase should not silently widen or
# narrow which checks run.
changed_paths() {
  { git -C "$REPO_ROOT" diff --name-only HEAD 2>/dev/null
    git -C "$REPO_ROOT" diff --cached --name-only 2>/dev/null
  } | sort -u
}

# The entire bug this script was fixed for lives in these four counters and how
# they become an exit code, so that translation is a pure function: no I/O, no
# globals, testable on a host with no toolchain at all (`--self-test`).
#
# BLOCKED is in the denominator: a check that never ran is not a check that
# passed, and the old "$PASS/$((PASS+FAIL))" line reported an environment where
# only ruff could start as a flawless `1/1 passed` — which is exactly how the
# platform gate came to paint a card green on the strength of one lint run.
# Explicit skips stay out of it: we asked for those.
verdict() {
    local pass="$1" fail="$2" blocked="$3" skip="$4" strict="$5"
    local summary="Result: $pass/$((pass + fail + blocked)) passed"
    if [ "$blocked" -gt 0 ]; then summary="$summary, $blocked blocked"; fi
    if [ "$skip" -gt 0 ]; then summary="$summary, $skip skipped"; fi
    echo "$summary"

    if [ "$fail" -gt 0 ]; then return 1; fi
    # Exit 2, distinct from a red check on purpose: nothing here says the code is
    # bad — it says the check never happened, so the caller (the platform gate)
    # must report "没跑成", not "通过" and not "未通过".
    if [ "$strict" = "1" ] && [ "$blocked" -gt 0 ]; then
        echo "STRICT: $blocked 项检查没能跑起来，这次检查没有真正跑完 —— 不判绿（exit 2）。"
        return 2
    fi
    # Backstop against "nothing ran, so nothing was wrong". Every path above
    # routes an un-runnable check to BLOCKED, so this should be unreachable
    # today — but a future --skip-<x> that lands in SKIP would sail straight
    # through, and `Result: 0/0 passed` is the shape the gate already shipped
    # once (card 14a2f2d3: four checks skipped, exit 0, card queued for
    # acceptance). Same guard check-action-pins.sh puts on an empty scan.
    if [ "$strict" = "1" ] && [ "$((pass + fail))" = "0" ]; then
        echo "STRICT: 一项检查都没有真正跑过，这次检查对代码没有任何结论 —— 不判绿（exit 2）。"
        return 2
    fi
    return 0
}

if [ "$SELF_TEST" = "1" ]; then
    # pass fail blocked skip strict | expected rc | expected summary
    SELF_TEST_CASES=(
        "1 0 0 1 1|0|Result: 1/1 passed, 1 skipped"
        "6 0 0 1 1|0|Result: 6/6 passed, 1 skipped"
        "3 0 3 1 1|2|Result: 3/6 passed, 3 blocked, 1 skipped"
        "3 0 3 1 0|0|Result: 3/6 passed, 3 blocked, 1 skipped"
        "1 0 3 0 1|2|Result: 1/4 passed, 3 blocked"
        "0 0 0 4 1|2|Result: 0/0 passed, 4 skipped"
        "0 0 0 4 0|0|Result: 0/0 passed, 4 skipped"
        "5 1 0 1 1|1|Result: 5/6 passed, 1 skipped"
        "0 1 3 0 1|1|Result: 0/4 passed, 3 blocked"
    )
    ST_FAIL=0
    for case in "${SELF_TEST_CASES[@]}"; do
        IFS='|' read -r counts want_rc want_summary <<<"$case"
        # shellcheck disable=SC2086
        got_out="$(verdict $counts)" && got_rc=0 || got_rc=$?
        got_summary="$(printf '%s\n' "$got_out" | head -1)"
        if [ "$got_rc" = "$want_rc" ] && [ "$got_summary" = "$want_summary" ]; then
            echo "  ok: [$counts] -> $want_rc, $want_summary"
        else
            echo "  BAD: [$counts] -> rc=$got_rc \"$got_summary\""
            echo "       expected rc=$want_rc \"$want_summary\""
            ST_FAIL=1
        fi
    done
    # scope_of: which halves of the tree a change set can affect.
    scope_case() {
        local want="$1"; shift
        local got; got="$(scope_of "$@")"
        if [ "$got" = "$want" ]; then
            echo "  ok: scope_of($*) -> $want"
        else
            echo "  BAD: scope_of($*) -> '$got', expected '$want'"
            ST_FAIL=1
        fi
    }
    scope_case "backend frontend"                                     # nothing known → run all
    scope_case "backend"  backend/app/main.py
    scope_case "frontend" frontend/src/App.vue
    scope_case "backend frontend" backend/app/main.py frontend/src/App.vue
    scope_case "none"     docs/README.md CLAUDE.md .github/workflows/test.yml
    scope_case "none"     e2e/smoke.spec.ts .claude/rules/frontend.md
    # A guard script is backend-affecting: it judges backend/app and its tests
    # live in backend/tests.
    scope_case "backend"  .claude/scripts/check-repo-rules.sh
    # Unclassified paths must default to backend, never to skipping it.
    scope_case "backend"  deploy/deploy-docker.sh
    scope_case "backend"  some/new/thing.py
    # One backend file among many harmless ones still pulls the suite in.
    scope_case "backend"  docs/a.md README.md backend/app/x.py

    [ "$ST_FAIL" = 0 ] && echo "self-test: ok" || echo "self-test: FAILED"
    exit "$ST_FAIL"
fi

# --full means "I want the lot" and overrides the scoping entirely.
if [ "$FULL" = "1" ]; then
    SCOPE="backend frontend"
else
    # shellcheck disable=SC2046  # word splitting is the point: one arg per path
    SCOPE="$(scope_of $(changed_paths))"
fi
echo "Scope: $SCOPE"

in_scope() { case " $SCOPE " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }

cd "$REPO_ROOT/backend"

# A check that couldn't run at all. Locally that's an environment limitation and
# must not block a developer (exit code unaffected); under --strict it means the
# gate didn't actually run, which is not a pass — see the summary at the bottom.
blocked() {
    echo "  BLOCKED: $1 (couldn't run here — environment limitation, not a code verdict)"
    ((++BLOCKED))
}

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
[[ "$STRICT" == "1" ]] && echo "Mode: STRICT (a check that can't run does not count as passed)" || true

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
# follow `check`/`format`, not precede it). On the scratch-venv path, `uv run`
# still has to provision a project-matching interpreter to host the command
# in — even though ruff itself needs no interpreter — so it's exposed to the
# same "no network to fetch a Python build" failure as pyright below; bound
# it with the same timeout so that failure degrades to SKIP instead of FAIL.
run_ruff() {
    if [ -x ".venv/bin/ruff" ]; then
        ".venv/bin/ruff" "$@" --cache-dir "$RUFF_CACHE_DIR"
    elif [ "${UV_RUN[*]}" = "uv run --no-sync" ]; then
        "${UV_RUN[@]}" ruff "$@" --cache-dir "$RUFF_CACHE_DIR"
    else
        timeout 120 "${UV_RUN[@]}" ruff "$@" --cache-dir "$RUFF_CACHE_DIR"
    fi
}

# --- ruff (lint + format, matching CI's test.yml lint job) ---
echo "==> ruff check + format"
if ! in_scope backend; then
    echo "  SKIP: ruff (nothing backend-affecting changed)"
    ((++SKIP))
elif [ -x ".venv/bin/ruff" ] || [ "${UV_RUN[*]}" = "uv run --no-sync" ]; then
    if run_ruff check . 2>&1 | tail -20 && run_ruff format --check . 2>&1 | tail -20; then
        echo "  PASS: ruff"
        ((++PASS))
    else
        echo "  FAIL: ruff (lint or format)"
        ((++FAIL))
    fi
elif run_ruff check . 2>&1 | tail -20 && run_ruff format --check . 2>&1 | tail -20; then
    echo "  PASS: ruff"
    ((++PASS))
else
    blocked "ruff (scratch-venv sync couldn't get it running in time)"
fi

# --- pyright ---
# Bounded ONLY on the scratch-venv path: a freshly-synced venv's pyright-
# python wrapper downloads a Node binary on first use, which can hang forever
# on a host with no network (or a cache path that ALSO resolves to a
# mismatched $HOME) — a timeout turns that into a clean, fast SKIP (an
# environment limitation, not a code issue) instead of stalling the whole
# gate. The inherited-venv path never hits this (nothing to download again).
echo "==> pyright"
if ! in_scope backend; then
    echo "  SKIP: pyright (nothing backend-affecting changed)"
    ((++SKIP))
elif [ "${UV_RUN[*]}" = "uv run --no-sync" ]; then
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
    blocked "pyright (scratch-venv sync couldn't get it running in time)"
fi

# --- alembic single head ---
# Parallel migrations fork the chain; `upgrade head` then refuses and the
# deploy aborts (four times on 2026-08-09/10). Mirror of CI's migration-heads
# job so the fork is caught before push. Reads the migration graph only — no
# DB. BLOCKED (not FAIL) when the venv can't import the app: environment, not code.
echo "==> alembic heads"
if ! in_scope backend; then
    echo "  SKIP: alembic heads (nothing backend-affecting changed)"
    ((++SKIP))
elif HEADS_OUT="$(timeout 60 "${UV_RUN[@]}" alembic heads 2>/dev/null)"; then
    HEADS_N="$(printf '%s\n' "$HEADS_OUT" | grep -c '(head)')"
    if [ "$HEADS_N" = "1" ]; then
        # The HEAD sentinel must name the tip — it is what turns a concurrent
        # migration PR into a git conflict instead of a silent alembic fork.
        TIP="$(printf '%s\n' "$HEADS_OUT" | awk '/\(head\)/ {print $1; exit}')"
        # Test for the file rather than redirecting from it and hoping: a failed
        # `<` redirection is the SHELL's error, not the command's, so bash prints
        # `alembic/HEAD: No such file or directory` regardless of the `2>/dev/null`
        # on `tr`. That raw line lands above the written-for-humans FAIL below and
        # reads as "check.sh is broken" rather than "move one line".
        SENTINEL=""
        if [ -f alembic/HEAD ]; then
            SENTINEL="$(tr -d '[:space:]' < alembic/HEAD)"
        fi
        if [ "$SENTINEL" = "$TIP" ]; then
            echo "  PASS: exactly one migration head, HEAD sentinel matches ($TIP)"
            ((++PASS))
        else
            echo "  FAIL: backend/alembic/HEAD says '${SENTINEL:-<missing>}' but the tip is $TIP"
            echo "        every migration moves this one-line file: echo $TIP > backend/alembic/HEAD"
            ((++FAIL))
        fi
    else
        printf '%s\n' "$HEADS_OUT" | sed 's/^/    /'
        echo "  FAIL: $HEADS_N migration heads — rechain your migration onto the current head (see .claude/rules/migrations.md)"
        ((++FAIL))
    fi
else
    blocked "alembic heads (couldn't run alembic in this environment)"
fi

# --- repo rules + migration fork ---
# Mirrors CI's repo-guards.yml job. These are pure bash/python3 stdlib — no
# venv, no DB, no network — so unlike everything above they don't degrade to
# BLOCKED just because the toolchain is thin: a violation they report IS a code
# verdict and must be a FAIL.
#
# The one exception is exit 127, "the guard itself isn't here" (script missing
# from the worktree, no python3 at all). That is not a verdict about the code,
# and reporting it as FAIL sends 芝士 off to hunt a code problem that doesn't
# exist — the exact confusion the BLOCKED state was introduced to end. So 127
# is BLOCKED, which under --strict still refuses to paint the gate green.
#
# Each guard runs ONCE, captured. The previous shape ran it a second time inside
# the `else` branch to show its output; under `set -euo pipefail` that second
# run's non-zero status aborted check.sh on the spot, so a repo-rules violation
# killed the script before it printed "FAIL: repo rules", before the migration
# fork and pytest checks ran, and before the summary line — exiting with the
# guard's own code instead of 1.
#
# The fork check compares against origin/main, which the alembic-heads check
# above CANNOT see: that one only proves THIS tree has one head, and two
# branches each adding a migration on the same parent both pass it. Cheap
# (reads the revision graph) so it runs every time.
run_guard() {
    local pass_label="$1" fail_label="$2"
    shift 2
    local out rc=0
    out="$("$@" 2>&1)" || rc=$?
    if [ "$rc" != 0 ] && [ -n "$out" ]; then
        printf '%s\n' "$out" | sed 's/^/    /'
    fi
    if [ "$rc" = 0 ]; then
        echo "  PASS: $pass_label"
        ((++PASS))
    elif [ "$rc" = 127 ]; then
        blocked "$fail_label (the guard script itself could not run here)"
    else
        echo "  FAIL: $fail_label"
        ((++FAIL))
    fi
}

migration_fork_guard() {
    # A missing script must look like a missing guard (127), not like the
    # violation python3 reports with exit 2 when it can't open the file.
    [ -f "$REPO_ROOT/.claude/scripts/check-migration-fork.py" ] || return 127
    (cd "$REPO_ROOT" && python3 .claude/scripts/check-migration-fork.py)
}

echo "==> repo guards"
run_guard "repo rules" "repo rules" \
    bash "$REPO_ROOT/.claude/scripts/check-repo-rules.sh"
run_guard "actions pinned to a commit SHA" "unpinned GitHub Action" \
    bash "$REPO_ROOT/.claude/scripts/check-action-pins.sh"
run_guard "metering proxy stays credential-hardened" "metering proxy hardening" \
    bash "$REPO_ROOT/.claude/scripts/check-metering-proxy.sh"

echo "==> migration fork (vs origin/main)"
run_guard "merging would not fork the alembic chain" "migration fork" \
    migration_fork_guard

# --- pytest ---
echo "==> pytest"
if ! in_scope backend; then
    echo "  SKIP: pytest (nothing backend-affecting changed)"
    ((++SKIP))
elif [ "$SKIP_TESTS" = "1" ]; then
    echo "  SKIP: pytest (--no-tests / SKIP_TESTS=1 — no usable Postgres on this host)"
    ((++SKIP))
# Fail fast (not present, not hanging) when the test DB is unreachable. Without
# this, every DB-touching test (the large majority) fails, and with
# --reruns 2 --reruns-delay 3 each one pays a ~6s retry tax before giving up —
# across ~3000 tests that blows well past any reasonable CI/gate timeout
# instead of reporting a clear, fast "no DB" failure.
elif ! DB_PROBE_OUT="$("${UV_RUN[@]}" python -c "
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
" 2>&1)"; then
    printf '%s\n' "$DB_PROBE_OUT" | sed 's/^/    /'
    # The probe answers two different questions with one exit code: "there is no
    # DB here" (a real pytest failure) vs "I couldn't even ask" (no interpreter,
    # unimportable app — same environment limitation as the checks above). Only
    # the first one is a verdict about the code, so only it may be a FAIL.
    if printf '%s' "$DB_PROBE_OUT" | grep -q "database unreachable"; then
        echo "  FAIL: pytest (no DB — skipped the run instead of paying the rerun-delay tax across ~3000 tests)"
        ((++FAIL))
    else
        blocked "pytest (couldn't even run the DB probe in this environment)"
    fi
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
    blocked "pytest (scratch-venv sync couldn't get it running in time)"
fi

# --- frontend ---
# New here: until now this script cd'd into backend/ and ran nothing else, so a
# frontend-only commit paid ~60s of pytest and got no frontend checks at all —
# the pre-commit hook documented that as "NOT a considered trade-off — a real
# gap". These are the two that .claude/rules/frontend.md certifies as runnable
# anywhere; `typecheck` and `build` are deliberately NOT here because they OOM
# on a small box, and a check that dies on memory would report as a code
# failure. CI's frontend.yml owns those.
echo "==> frontend"
if ! in_scope frontend; then
    echo "  SKIP: eslint + stylelint (no frontend changes)"
    ((++SKIP))
elif ! command -v pnpm >/dev/null 2>&1; then
    blocked "frontend lint (pnpm not installed)"
elif [ ! -d "$REPO_ROOT/frontend/node_modules" ]; then
    blocked "frontend lint (node_modules missing — run pnpm install in frontend/)"
elif (cd "$REPO_ROOT/frontend" && pnpm run lint 2>&1 | tail -15 && pnpm run lint:style 2>&1 | tail -15); then
    echo "  PASS: eslint + stylelint"
    ((++PASS))
else
    echo "  FAIL: frontend lint (eslint or stylelint)"
    ((++FAIL))
fi

# --- summary ---
echo ""
RC=0
verdict "$PASS" "$FAIL" "$BLOCKED" "$SKIP" "$STRICT" || RC=$?
exit "$RC"
