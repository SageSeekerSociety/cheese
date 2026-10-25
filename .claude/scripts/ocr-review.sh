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
# WHERE THE MODEL COMES FROM: the same pair every sandbox already has —
# $CHEESE_API and $CHEESE_TOKEN. The base URL is `$CHEESE_API/llm`; the client
# appends `/v1`, so `$CHEESE_API/llm/v1` would ask for `/llm/v1/v1/messages`
# and get a 404. Only models the gateway allows for this project work; the
# Claude ids are blocked on it.
#
# Usage:
#   .claude/scripts/ocr-review.sh --from REF --to REF [--out FILE]
#   .claude/scripts/ocr-review.sh BASE HEAD [--out FILE]
#
# Environment (defaults in parentheses):
#   OCR_MODEL            model id (deepseek-flash)
#   OCR_EFFORT           review rounds per file: low is 1, medium 2, high 3 (low)
#   OCR_MAX_TOOLS        tool rounds per subtask; RAISES the 100-row template
#                        default and cannot lower it, so leaving this unset is
#                        the only way to get less than 100 (unset)
#   OCR_TIMEOUT          wall clock in minutes for the whole run (25). It is not a
#                        hard kill: OCR stops dispatching new groups at it and lets
#                        the ones in flight finish, so the run can end sooner (its
#                        own dispatch budget is checked between rounds). Too low a
#                        value is worse than too high — 10 minutes on a five-file
#                        change ended every group as `timeout` and produced nothing
#   OCR_CONCURRENCY      concurrent subtasks (8)
#   OCR_TOKEN_BUDGET     total token budget; 0 = unlimited (0)
#   OCR_VERSION          open-code-review version to install and run (1.13.3)
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
CONCURRENCY="${OCR_CONCURRENCY:-8}"
TOKEN_BUDGET="${OCR_TOKEN_BUDGET:-0}"

# The published linux-x64 binary for 1.13.3. A different platform or version is
# a deliberate edit here, not something to discover at runtime.
PINNED_SHA="843be0e56fdff0a5ca6f86c5866f942d6a372dff1c0ca19e7893d72495abf45c"
PINNED_PLATFORM="linux-x64"

die() { echo "ocr-review: $*" >&2; exit 1; }

FROM=""
TO=""
OUT=""
while [ $# -gt 0 ]; do
  case "$1" in
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
[ -n "${CHEESE_API:-}" ] || die "CHEESE_API is not set; this repo's model channel comes from the platform environment"
[ -n "${CHEESE_TOKEN:-}" ] || die "CHEESE_TOKEN is not set; run inside a task sandbox"

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
CLI="$CACHE/node_modules/.bin/ocr"

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

# Results live beside the cached binary, not inside the worktree: a review
# tree is disposable, and an untracked result file inside it reads as a change
# somebody forgot to commit. In a task worktree `.git` is also a file, so
# "$REPO/.git" is not a directory to write into.
# `tr` would fold every separator to the same character, so `a/b..HEAD` and
# `a.b..HEAD` would land on one path and the second run would overwrite the
# first's result and log. `sha256sum` keeps them distinct.
OUT="${OUT:-$CACHE/results/$(printf '%s..%s' "$FROM" "$TO" | sha256sum | cut -c1-16).json}"
mkdir -p "$(dirname "$OUT")"

OCR_LLM_URL="${CHEESE_API%/}/llm" \
OCR_LLM_TOKEN="$CHEESE_TOKEN" \
OCR_LLM_MODEL="$MODEL" \
  "$CLI" review \
    --repo "$REPO" \
    --from "$FROM" --to "$TO" \
    --format json --audience agent \
    ${MAX_TOOLS:+--max-tools "$MAX_TOOLS"} \
    --effort "$EFFORT" \
    --timeout "$TIMEOUT_MIN" \
    --concurrency "$CONCURRENCY" \
    --max-tokens-budget "$TOKEN_BUDGET" \
  >"$OUT" 2>"${OUT%.json}.log" || {
    status=$?
    echo "ocr-review: review exited $status; log: ${OUT%.json}.log" >&2
    [ -s "$OUT" ] && echo "ocr-review: partial result: $OUT" >&2
    exit 1
  }

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
