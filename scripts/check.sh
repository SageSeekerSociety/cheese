#!/usr/bin/env bash
# check.sh — the one entry point at the repo root for "how do I verify this?".
#
# WHY THIS EXISTS: the instructions an agent reads name the command to run
# before handing a change off, and every copy of that answer pointed at a file
# that could be deleted from under it. `.claude/scripts/check.sh` was removed in
# #518 while README, `docs/topics/沙箱跑测试标准方法.md` and the Required CI path
# test still named it (`.github/scripts/check.py`, in the same table, had been
# gone even longer), and nothing noticed: the path table only feeds pattern
# matching, so a deleted path still selects its suite and reads as true.
# `task check` is not on a work machine at all (no `task` binary), and the
# repository root has no pyproject/package.json/pytest.ini, so "run something at
# the root" had no true answer — the first attempt spent half an hour on
# `No module named pytest` (#1965). This script is that answer: it exists, it
# needs nothing beyond python3, and it names the review entry the same way.
#
# Usage: check.sh                        the quick tier of ci-fast, from the root
#        check.sh review <PR> [flags]    start an independent review (prepare)
#        check.sh --self-test            prove the dispatch still works
#
# Exit status: whatever the dispatched command returns (0 pass), 2 for usage.
set -uo pipefail

SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
REPO_ROOT="$(cd "$(dirname "$SELF")/.." && pwd)"

usage() {
  cat >&2 <<'EOF'
usage: check.sh                        quick tier of ci-fast, from the repo root
       check.sh review <PR> [flags]    independent review (pr-review.sh prepare)
       check.sh --self-test            prove the dispatch still works
EOF
}

if [ "${1:-}" = "--self-test" ]; then
  self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }

  # Dispatch is tested against a copy of this script inside a fake tree: the
  # root is derived from the script's own location, so the stubs below are
  # exactly what it will exec. The real quick tier must not run here.
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  mkdir -p "$tmp/repo/scripts" "$tmp/repo/.github/scripts" \
    "$tmp/repo/.claude/scripts" "$tmp/elsewhere"
  cp "$SELF" "$tmp/repo/scripts/check.sh"
  fake_root="$(cd "$tmp/repo" && pwd)"

  # No arguments: quick tier, from the repo root, exit code untouched.
  cat > "$tmp/repo/.github/scripts/ci-fast.py" <<'PY'
import os
import sys

with open(os.environ["CHECK_SH_LOG"], "w", encoding="utf-8") as fh:
    fh.write(f"{os.getcwd()}\n{' '.join(sys.argv[1:])}\n")
sys.exit(7)
PY
  log="$tmp/quick.log"
  code=0
  ( cd "$tmp/elsewhere" && CHECK_SH_LOG="$log" bash "$tmp/repo/scripts/check.sh" ) \
    || code=$?
  [ "$code" -eq 7 ] || self_fail "no-arg must pass the child's exit code through (got $code)"
  [ "$(sed -n 1p "$log")" = "$fake_root" ] \
    || self_fail "no-arg must run from the repo root (got $(sed -n 1p "$log"))"
  [ "$(sed -n 2p "$log")" = "--tier quick" ] \
    || self_fail "no-arg must run the quick tier (got $(sed -n 2p "$log"))"

  # review <PR> [flags] is the prepare step, with the arguments intact.
  cat > "$tmp/repo/.claude/scripts/pr-review.sh" <<'SH'
#!/usr/bin/env bash
printf '%s\n' "$*" > "$CHECK_SH_LOG"
exit 9
SH
  chmod +x "$tmp/repo/.claude/scripts/pr-review.sh"
  log="$tmp/review.log"
  code=0
  CHECK_SH_LOG="$log" bash "$tmp/repo/scripts/check.sh" review 4242 --since deadbeef \
    || code=$?
  [ "$code" -eq 9 ] || self_fail "review must pass the child's exit code through (got $code)"
  [ "$(cat "$log")" = "prepare 4242 --since deadbeef" ] \
    || self_fail "review must run prepare with the PR (got $(cat "$log"))"

  # A missing PR is a usage error, not a guess.
  code=0
  bash "$tmp/repo/scripts/check.sh" review >/dev/null 2>&1 || code=$?
  [ "$code" -eq 2 ] || self_fail "review without a PR must exit 2 (got $code)"

  # The references this entry replaced, in the files that carry them: each must
  # name the live path, or the reader is sent back to a deleted file.
  grep -qF './scripts/check.sh' "$REPO_ROOT/README.md" \
    || self_fail "README.md no longer names ./scripts/check.sh"
  grep -qF '`scripts/check.sh`' "$REPO_ROOT/docs/topics/沙箱跑测试标准方法.md" \
    || self_fail "the sandbox testing note no longer names scripts/check.sh"
  grep -qF '"scripts/check.sh"' "$REPO_ROOT/.github/scripts/test_required_ci.py" \
    || self_fail "test_required_ci.py no longer names scripts/check.sh"
  grep -qF 'scripts/check.sh review' "$REPO_ROOT/AGENTS.md" \
    || self_fail "AGENTS.md no longer names scripts/check.sh review"
  [ -f "$REPO_ROOT/.github/scripts/ci-fast.py" ] \
    || self_fail ".github/scripts/ci-fast.py is missing"
  [ -x "$REPO_ROOT/.claude/scripts/pr-review.sh" ] \
    || self_fail ".claude/scripts/pr-review.sh is missing or not executable"

  echo "PASS: check.sh self-test (quick tier from the root, exit codes, review"
  echo "      dispatch, usage error, and the files that name this entry)"
  exit 0
fi

case "${1:-}" in
  "")
    cd "$REPO_ROOT" || { echo "check.sh: cannot enter $REPO_ROOT" >&2; exit 1; }
    exec python3 .github/scripts/ci-fast.py --tier quick
    ;;
  review)
    shift
    if [ "$#" -lt 1 ]; then
      usage
      exit 2
    fi
    exec "$REPO_ROOT/.claude/scripts/pr-review.sh" prepare "$@"
    ;;
  -h|--help)
    usage
    exit 0
    ;;
  *)
    echo "check.sh: unknown command: $1" >&2
    usage
    exit 2
    ;;
esac
