#!/usr/bin/env bash
# ocr-review.sh — run open-code-review over a commit range, with this repo's
# model channel and this repo's defaults.
#
# WHY: the review method used to be a long prompt handed to a seat's
# subagents. Its budgets (tool calls, rounds, wall clock) were advice the model
# could ignore: a method that asked for 20 tool calls spent 43 and 16 minutes.
# open-code-review carries its own loop — it counts tool rounds per file, stops
# dispatching groups once a token budget is spent, and enforces a wall clock —
# so the limits are enforced by the thing that runs the review, not by the text
# it was given. What stays ours is the judgement: which findings count as
# blocker or major, and what goes into the card.
#
# WHERE THE MODEL COMES FROM: the sandbox's platform socket,
# $CHEESE_PLATFORM_SOCKET, which needs no credential: the platform knows whose
# the call is by which socket it came in on, and charges it like any other call
# of the session. open-code-review takes a URL, so platform_loopback.py gives
# it one on a loopback port with a random prefix. The sandbox's $CHEESE_TOKEN
# is refused by the model routes, so there is no other way in. The base ends
# in `/llm`; the client appends `/v1`, so a base ending in `/llm/v1` would ask
# for `/llm/v1/v1/messages` and get a 404. Only models the gateway
# allows for this project work; the Claude ids are blocked on it.
#
# Usage:
#   .claude/scripts/ocr-review.sh --from REF --to REF [--out FILE]
#   .claude/scripts/ocr-review.sh BASE HEAD [--out FILE]
#   .claude/scripts/ocr-review.sh --self-test
#
# Environment (defaults in parentheses):
#   OCR_MODEL            model id (deepseek-flash)
#   OCR_EFFORT           review rounds per file: low is 1, medium 2, high 3 (low)
#   OCR_MAX_TOOLS        tool rounds per subtask; RAISES the 100-row template
#                        default and cannot lower it, so leaving this unset is
#                        the only way to get less than 100 (unset)
#   OCR_TIMEOUT          the OCR run's own budget, in minutes (25). It is NOT a
#                        whole-run wall clock: OCR multiplies it by the effort's
#                        round count (low 1, medium 2, high 3) and applies the
#                        product to EACH file group, so with concurrency 8 the run
#                        lasts as many batches as it has groups, each batch getting
#                        that long. Measured on 1.13.3's source (agent.go:
#                        `ConcurrentTaskTimeout * ReviewRounds`) and by running it.
#                        It is not a hard kill either: OCR stops dispatching new
#                        groups at it and lets the ones in flight finish. Too low a
#                        value is worse than too high — 10 minutes on a five-file
#                        change ended every group as `timeout` and produced nothing
#   OCR_WALL_CLOCK       hard ceiling in minutes on the whole run (25), enforced by
#                        `timeout` in this script rather than by OCR: at it OCR is
#                        sent SIGTERM, which OCR treats as a graceful stop — it
#                        writes the findings it has and the coverage ledger, then
#                        exits. A run cut this way is a partial result, not a run
#                        that produced nothing; the summary line says so. It exists
#                        because the platform stops a backgrounded command on its
#                        own timeout and a killed process writes nothing at all.
#                        The worst case is this plus OCR_GRACE, and that sum has
#                        to fit under the platform's own limit on the command with
#                        room for the install and the setup before this run: the
#                        platform kills the process and nothing is written, which
#                        is the failure this ceiling is here to get in front of.
#                        0 disables it. A caller that knows its own deadline should
#                        pass one and set OCR_WALL_CLOCK under it.
#   OCR_GRACE            seconds to let OCR finish writing after SIGTERM before it
#                        is killed (60); SIGKILL after this loses the partial result
#   OCR_CONCURRENCY      concurrent subtasks (8)
#   OCR_TOKEN_BUDGET     cap on total tokens (input+output) for the whole run
#                        (8000000). Checked before every LLM round: a group over
#                        it gets one final round to submit findings and is
#                        reported as failed(budget), and no further group is
#                        dispatched. Partial results are still published. 0 =
#                        unlimited. The default is about twice the largest honest
#                        run measured on this repo (10 files, 3.5M tokens), so it
#                        is a brake on a runaway rather than a spend policy. The
#                        wall clock is not enough on its own: it stops dispatching
#                        and the groups already in flight run to completion and
#                        bill for every round of it
#   OCR_VERSION          open-code-review version to install and run (1.13.3)
#   OCR_CLI              run this executable instead of installing and running
#                        the pinned one. The install, the sha256 check and the
#                        package-version check are all skipped: the point of the
#                        pin is what runs by DEFAULT, and a path given here is a
#                        deliberate choice by the caller. `--self-test` uses it
#                        to exercise the wall clock without a 54 MB download
#
# The binary is not committed: it is 54 MB, and a pinned npm install into a
# per-version cache takes about seven seconds. Before anything runs, the native
# binary's sha256 and the package's own version are both checked, so a published
# artifact that changed under the same version number does not silently become
# this repo's reviewer.
#
# Exit status: 0 review ran (partial results included), 1 setup or run failed.
set -euo pipefail

VERSION="${OCR_VERSION:-1.13.3}"
MODEL="${OCR_MODEL:-deepseek-flash}"
EFFORT="${OCR_EFFORT:-low}"
MAX_TOOLS="${OCR_MAX_TOOLS:-}"
TIMEOUT_MIN="${OCR_TIMEOUT:-25}"
WALL_CLOCK_MIN="${OCR_WALL_CLOCK:-25}"
GRACE_S="${OCR_GRACE:-60}"
CONCURRENCY="${OCR_CONCURRENCY:-8}"
TOKEN_BUDGET="${OCR_TOKEN_BUDGET:-8000000}"

# The published linux-x64 binary for 1.13.3. A different platform or version is
# a deliberate edit here, not something to discover at runtime.
PINNED_SHA="843be0e56fdff0a5ca6f86c5866f942d6a372dff1c0ca19e7893d72495abf45c"
PINNED_PLATFORM="linux-x64"

die() { echo "ocr-review: $*" >&2; exit 1; }

# Run the CLI under the wall clock, writing the result to $OUT and the run's own
# output to the log beside it. $1 is the command as an array's first element.
#
# The run gets a session of its own: `setsid` puts the wrapper, the native
# binary and the git subprocesses it spawns in one process group, which is what
# lets the wall clock reach all of them. Anything left in this shell's group
# would outlive the ceiling and keep billing. The signal is SIGTERM because OCR
# handles it — it writes the findings it has and the coverage ledger before
# exiting, which is the whole point of having a ceiling at all.
#
# 0 disables the ceiling, for a caller that knows its own deadline and wants
# OCR's own accounting to be the only thing that stops it. Without
# --foreground, `timeout` runs the child in a process group of its own and
# signals the group, so nothing under the CLI outlives the ceiling;
# --kill-after fires only when the grace period runs out.
#
# Prints a note on stdout when the ceiling is what ended the run, so the caller
# can say so: a run cut this way is a partial result, not a failed one.
run_ocr() {
  local out=$1 log=$2
  shift 2
  local status
  set +e
  if [ "$WALL_CLOCK_MIN" = 0 ]; then
    "$@" >"$out" 2>"$log"
    status=$?
  else
    # awk, not $(( )): the ceiling may be a fraction of a minute in a test, and
    # bash's arithmetic is integer-only.
    local ceiling
    ceiling=$(awk -v m="$WALL_CLOCK_MIN" 'BEGIN { printf "%d", m * 60 }')
    # -w: wait for the child even when setsid had to fork (it does when the
    # caller is already a process-group leader), so the status below is the
    # run's, not setsid's.
    setsid -w timeout --signal=TERM --kill-after="$GRACE_S" "${ceiling}s" \
      "$@" >"$out" 2>"$log"
    status=$?
    [ "$status" != 124 ] || printf ' (closed at the %sm wall clock)' "$WALL_CLOCK_MIN"
  fi
  set -e
  return "$status"
}

# Prove the ceiling, offline and without the 54 MB install: a stand-in CLI that
# writes a partial result, hangs, and on SIGTERM writes what it has — which is
# what OCR does. Three things have to hold, and each is a mutation the next
# reader would otherwise make by accident: the run is stopped at the ceiling at
# all, it is stopped with a signal OCR can act on (SIGKILL would leave the
# partial result unwritten), and the grandchild it spawned goes with it (or the
# ceiling stops the parent and the children keep calling the model).
self_test() {
  local dir fail=0 fake got killed
  dir=$(mktemp -d "${TMPDIR:-/tmp}/ocr-review-self-test.XXXXXX") || return 1
  trap 'rm -rf "$dir"' RETURN
  fake="$dir/ocr"
  cat >"$fake" <<'FAKE'
#!/usr/bin/env bash
printf '{"status":"partial","summary":{"files_reviewed":1}}\n'
sleep 9137 &
trap 'printf "{\"status\":\"partial-after-term\"}\n"; exit 1' TERM
while :; do sleep 1; done
FAKE
  chmod +x "$fake"

  # Ceiling 3s, grace 5s — and a watch of 20s around it. A ceiling that stopped
  # working would leave the run hanging rather than failing, and a self-test that
  # hangs in CI is worse than one that goes red, so the wait is bounded here
  # instead of by the lane's own timeout.
  WALL_CLOCK_MIN=0.05 GRACE_S=5 \
    run_ocr "$dir/out.json" "$dir/out.log" "$fake" >"$dir/note" &
  local run_pid=$!
  local waited=0
  while kill -0 "$run_pid" 2>/dev/null && [ "$waited" -lt 20 ]; do
    sleep 0.5; waited=$((waited + 1))   # 20 x 0.5s = 10s
  done
  if kill -0 "$run_pid" 2>/dev/null; then
    echo "self-test FAILED: nothing stopped the run; it was still going after 10s" >&2
    kill "$run_pid" 2>/dev/null || true
    pkill -f "sleep 9137" 2>/dev/null || true
    return 1
  fi
  got=$(cat "$dir/out.json" 2>/dev/null || true)
  case "$got" in
    *partial-after-term*) ;;
    *) echo "self-test FAILED: the run was not stopped with a signal OCR could act on (got: ${got:-nothing})" >&2; fail=1 ;;
  esac
  grep -q "closed at the 0.05m wall clock" "$dir/note" \
    || { echo "self-test FAILED: the ceiling did not report that it was what stopped the run"; fail=1; }
  sleep 1
  if killed=$(pgrep -f "sleep 9137"); then
    echo "self-test FAILED: a grandchild outlived the ceiling (pids: $killed)" >&2
    kill $killed 2>/dev/null || true
    fail=1
  fi

  # A ceiling of 0 must not be a ceiling at all. What this asserts is that the
  # disarmed path does not report itself as an active ceiling — a 0 that reached
  # the arithmetic would become a 0s ceiling and stop the run at once, and the
  # absence of the note is what tells the two apart without a stopwatch.
  WALL_CLOCK_MIN=0 GRACE_S=5 run_ocr "$dir/out2.json" "$dir/out2.log" \
    timeout 2 "$fake" >"$dir/note2" || true
  [ ! -s "$dir/note2" ] \
    || { echo "self-test FAILED: a disarmed ceiling reported itself as an active one"; fail=1; }
  pkill -f "sleep 9137" 2>/dev/null || true

  [ "$fail" = 0 ] || return 1
  echo "self-test ok: the wall clock stops the run with SIGTERM, reports it, and takes the grandchildren with it"
}

FROM=""
TO=""
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --self-test) self_test; exit $? ;;
    --from) FROM="$2"; shift 2 ;;
    --to) TO="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    -h|--help)
      # Stop at the first line that is no longer a comment, instead of a hard-coded
      # range: the header invites edits (a changed default), and a fixed range
      # silently truncates or overruns the help text when it grows or shrinks.
      sed -n '3,/^[^#]/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' >&2
      exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) if [ -z "$FROM" ]; then FROM="$1"; elif [ -z "$TO" ]; then TO="$1"; else die "unexpected argument $1"; fi; shift ;;
  esac
done
[ -n "$FROM" ] && [ -n "$TO" ] || die "need --from and --to (or two positional refs)"
[ -S "${CHEESE_PLATFORM_SOCKET:-}" ] \
  || die "no platform socket (\$CHEESE_PLATFORM_SOCKET): run inside a task sandbox; a sandbox started before the socket existed gets it when its executor is next installed"
# The helper lives exactly as long as this script.
exec {LOOPBACK}< <(exec python3 "$(dirname "${BASH_SOURCE[0]}")/platform_loopback.py")
LOOPBACK_PID=$!
trap 'kill "$LOOPBACK_PID" 2>/dev/null' EXIT
read -r -t 10 -u "$LOOPBACK" BASE || die "platform_loopback.py did not start"
LLM_URL="$BASE/llm" LLM_TOKEN="machine"

REPO="$(git rev-parse --show-toplevel)"
# A partial clone (blob:none) carries commits but not file contents, and fetches
# a missing blob on demand. The review greps the whole tree at the target
# commit: with the target checked out as HEAD those blobs are on disk, and grep
# works; against any other ref every missing blob is a network round trip, and
# one failed fetch turns into a search the model retries until the wall clock
# cuts it (that cost a run 25 minutes and 4.1M tokens for zero findings). So a
# partial clone may only be reviewed at HEAD, and the base side of the diff is
# materialized first, while a failure is still cheap and visible here.
if [ -n "$(git -C "$REPO" config --get remote.origin.partialclonefilter 2>/dev/null)" ]; then
  [ "$TO" = "HEAD" ] || die "partial clone: review --to must be HEAD (the checked-out commit), got $TO"
  # `--name-only` walks trees and commits, both of which a blob:none clone has;
  # it would succeed without fetching a single base-side blob, and the failure
  # would land later, inside OCR, as a search error the model retries. A plain
  # `git diff` reads the contents, so the fetch happens here or fails here.
  git -C "$REPO" diff "$FROM" HEAD >/dev/null 2>&1 \
    || die "partial clone: could not materialize the base side of $FROM..HEAD"
fi
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cheese-ocr/$VERSION"
BIN="$CACHE/node_modules/@alibaba-group/ocr-$PINNED_PLATFORM/bin/opencodereview"

install_ocr() {
  command -v npm >/dev/null 2>&1 || die "npm is not on PATH; the pinned OCR install needs it"
  mkdir -p "$CACHE"
  # Both streams go to the log rather than /dev/null: the failure below covers
  # registry down, TLS/CA error, a 404 on the version and a full disk, and the
  # install's own last lines are the only thing that says which it was.
  local log="$CACHE/install.log"
  if ! npm install --prefix "$CACHE" --no-save --registry=https://registry.npmjs.org \
       "@alibaba-group/open-code-review@$VERSION" >"$log" 2>&1; then
    echo "ocr-review: could not install $VERSION; last lines of $log:" >&2
    tail -5 "$log" >&2
    exit 1
  fi
}

# A caller that named an executable is taken at its word: the pin below is what
# protects the default path, not a caller's own choice. `--self-test` uses it to
# exercise the wall clock without a 54 MB download.
if [ -z "${OCR_CLI:-}" ]; then
  CLI="$CACHE/node_modules/.bin/ocr"
  if [ ! -x "$BIN" ]; then
    install_ocr
  fi
  ACTUAL_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
  if [ "$ACTUAL_SHA" != "$PINNED_SHA" ]; then
    # A cached binary that differs may be a stale install of the same version.
    # Reinstall once; a second mismatch is the registry's answer, and this repo
    # must not run a reviewer it did not pin.
    install_ocr
    ACTUAL_SHA="$(sha256sum "$BIN" | cut -d' ' -f1)"
    [ "$ACTUAL_SHA" = "$PINNED_SHA" ] \
      || die "binary sha256 $ACTUAL_SHA does not match the pinned $PINNED_SHA"
  fi
  # The hash above covers the native binary. What actually runs is the JS wrapper
  # in the package, which picks that binary and execs it, so this is the other half
  # of the pin: the installed package's own version, read from npm's metadata. It
  # is a version rather than a hash — the wrapper changes with the release, and its
  # bytes under one version number are the registry's to keep honest.
  INSTALLED_VERSION="$(node -e 'process.stdout.write(require(process.argv[1]).version)' \
    "$CACHE/node_modules/@alibaba-group/open-code-review/package.json" 2>/dev/null || true)"
  [ "$INSTALLED_VERSION" = "$VERSION" ] \
    || die "installed package version '$INSTALLED_VERSION' is not the pinned $VERSION"
else
  CLI="$OCR_CLI"
fi

# Results live beside the cached binary, not inside the worktree: a review
# tree is disposable, and an untracked result file inside it reads as a change
# somebody forgot to commit. In a task worktree `.git` is also a file, so
# "$REPO/.git" is not a directory to write into.
# `tr` would fold every separator to the same character, so `a/b..HEAD` and
# `a.b..HEAD` would land on one path and the second run would overwrite the
# first's result and log. `sha256sum` keeps them distinct.
OUT="${OUT:-$CACHE/results/$(printf '%s..%s' "$FROM" "$TO" | sha256sum | cut -c1-16).json}"
mkdir -p "$(dirname "$OUT")"

RUN=(
  env
  "OCR_LLM_URL=$LLM_URL"
  "OCR_LLM_TOKEN=$LLM_TOKEN"
  "OCR_LLM_MODEL=$MODEL"
  "$CLI" review
    --repo "$REPO"
    --from "$FROM" --to "$TO"
    --format json --audience agent
    --effort "$EFFORT"
    --timeout "$TIMEOUT_MIN"
    --concurrency "$CONCURRENCY"
    --max-tokens-budget "$TOKEN_BUDGET"
)
[ -z "$MAX_TOOLS" ] || RUN+=(--max-tools "$MAX_TOOLS")

set +e
ceiled=$(run_ocr "$OUT" "${OUT%.json}.log" "${RUN[@]}")
status=$?
set -e
if [ "$status" != 0 ]; then
  echo "ocr-review: review exited $status$ceiled; log: ${OUT%.json}.log" >&2
  [ -s "$OUT" ] && echo "ocr-review: partial result: $OUT" >&2
  exit 1
fi

python3 - "$OUT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
s = d.get("summary") or {}
print(json.dumps({
    "status": d.get("status"),
    "files_reviewed": s.get("files_reviewed"),
    "comments": s.get("comments"),
    "tokens": s.get("total_tokens"),
    "elapsed": s.get("elapsed"),
    "result": sys.argv[1],
}, ensure_ascii=False))
PY
