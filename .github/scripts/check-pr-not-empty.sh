#!/usr/bin/env bash
# check-pr-not-empty.sh — a pull request that changes no file must not be green.
#
# WHY: four pull requests reached main changing zero files — #608, #610, #611
# and #618 — each one merged, each one delivering nothing. #610 was meant to
# make every document declare whether it is current or a record, and none of it
# is in main; #608 had to be re-delivered by a second pull request written for
# that purpose. Every time it was found by reading main weeks later.
#
# Nothing between "open" and "merged" was looking at this. Every other check in
# the repo judges a tree, and an empty pull request has the same tree as its
# base — so all of them pass, honestly, and the merge is green all the way down
# while delivering nothing. The cause is fixed (#620), but a cause being fixed
# is not the same as the outcome being noticed, and this is the check that
# notices.
#
# THE RANGE IS merge-base(base, head)..head, which is the range GitHub itself
# uses to compute a pull request's file list. Two neighbouring questions are
# deliberately not asked:
#
#   `git diff <base-tip> <head>` — a two-dot diff from the base's *current* tip.
#   Once main moves on, that diff shows main's new commits as deletions, so a
#   pull request delivering nothing looks like a change. It would pass exactly
#   the pull requests this guard exists to fail.
#
#   `git diff <head>^ <head>` — what one commit added on top of its first
#   parent. That disagrees with the pull request's file list whenever the head
#   is a merge: a branch carrying a real change that then merges in a base tip
#   whose content is unchanged has an empty first-parent diff, and would be
#   failed for no reason. The self-test builds that branch and requires it to
#   pass.
#
# THE CRITERION IS THE FILE COUNT, not lines. A change of permissions alone is
# a real change with a net line count of zero (`git diff --numstat` prints
# `0  0  <path>`), so a lines-changed rule would fail it. A file the diff
# names is a file the merge delivers, whatever happened inside it — a mode bit,
# a whitespace fix, a rename.
#
# The cost of judging the count alone is stated plainly: a pull request whose
# diff is non-empty but wrong still passes. That is not this check's job.
#
# Usage: check-pr-not-empty.sh <base-sha> <head-sha> [repo-dir]
#        check-pr-not-empty.sh --self-test    prove it still fires
set -euo pipefail

judge() {
  local repo="$1" base="$2" head="$3"
  local merge_base files count

  if ! merge_base="$(git -C "$repo" merge-base "$base" "$head")"; then
    echo "FAIL: $base and $head share no common ancestor — there is no pull request range to judge." >&2
    return 1
  fi

  # quotepath=false so a CJK filename prints as itself rather than as octal
  # escapes; this repo has plenty of them and the list is read by people.
  files="$(git -C "$repo" -c core.quotepath=false diff --name-only "$merge_base" "$head")"

  if [ -z "$files" ]; then
    cat >&2 <<EOF
FAIL: this pull request changes no files.

  base      $base
  head      $head
  compared  $merge_base..$head   (the range GitHub uses for the Files tab)

Merging it would put a commit message on main and nothing else. Either the work
never left the machine it was written on, or the branch was reset over it — push
the branch again and confirm the pull request's Files tab is not empty before
asking for a merge.
EOF
    return 1
  fi

  count="$(printf '%s\n' "$files" | wc -l | tr -d '[:space:]')"
  echo "OK: $count file(s) changed in $merge_base..$head"
  printf '%s\n' "$files"
}

if [ "${1:-}" != "--self-test" ]; then
  [ $# -ge 2 ] || { sed -n '/^# Usage:/,/self-test/p' "$0" >&2; exit 2; }
  judge "${3:-$PWD}" "$1" "$2"
  exit $?
fi

# --- self-test ---------------------------------------------------------------
# A guard nobody checks is a guard that can stop matching and still look like a
# clean repo — which is the failure mode this guard exists to prevent. So it
# builds each shape out of real commits and requires the verdict.
self_fail() { echo "SELF-TEST FAIL: $*" >&2; exit 1; }

tmp="$(mktemp -d "${TMPDIR:-/tmp}/check-pr-not-empty.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
repo="$tmp/repo"

git init -q "$repo"
git -C "$repo" config user.email ci@example.com
git -C "$repo" config user.name ci
git -C "$repo" config commit.gpgsign false

commit_all() { git -C "$repo" add -A && git -C "$repo" commit -q -m "$1"; }

echo base > "$repo/a.txt"
commit_all base
BASE="$(git -C "$repo" rev-parse HEAD)"

expect_pass() {
  judge "$repo" "$1" "$2" >/dev/null 2>&1 || self_fail "$3"
}
expect_fail() {
  judge "$repo" "$1" "$2" >/dev/null 2>&1 && self_fail "$3"
  return 0
}

# 1. An ordinary pull request.
git -C "$repo" checkout -q -B real "$BASE"
echo work > "$repo/b.txt"
commit_all "a real change"
REAL="$(git -C "$repo" rev-parse HEAD)"
expect_pass "$BASE" "$REAL" "a pull request that adds a file must pass"

# 2. The shape that reached main four times: a commit with the base's tree.
git -C "$repo" checkout -q -B empty "$BASE"
git -C "$repo" commit -q --allow-empty -m "empty working-copy commit"
EMPTY="$(git -C "$repo" rev-parse HEAD)"
expect_fail "$BASE" "$EMPTY" "a pull request whose diff is empty must fail"

# 3. …and it must still fail once main has moved on. A two-dot diff from main's
#    tip reports main's own new commits here, and would call this a change.
git -C "$repo" checkout -q -B main_ "$BASE"
echo later > "$repo/c.txt"
commit_all "someone else's merge, after this branch opened"
MOVED="$(git -C "$repo" rev-parse HEAD)"
[ -n "$(git -C "$repo" diff --name-only "$MOVED" "$EMPTY")" ] \
  || self_fail "fixture broken: the two-dot diff was supposed to be non-empty here"
expect_fail "$MOVED" "$EMPTY" "an empty pull request must fail after the base advanced"

# 4. The deliberate non-false-positive: a branch carrying a real change that
#    merges back a base tip whose content is unchanged. Its first-parent diff is
#    empty; its pull request is not.
git -C "$repo" checkout -q -B synced "$BASE"
echo work > "$repo/d.txt"
commit_all "a real change"
git -C "$repo" checkout -q -B unchanged_base "$BASE"
git -C "$repo" commit -q --allow-empty -m "a base tip that changes nothing"
UNCHANGED_BASE="$(git -C "$repo" rev-parse HEAD)"
git -C "$repo" checkout -q synced
git -C "$repo" merge -q --no-edit "$UNCHANGED_BASE"
MERGED="$(git -C "$repo" rev-parse HEAD)"
[ -z "$(git -C "$repo" diff --name-only "${MERGED}^" "$MERGED")" ] \
  || self_fail "fixture broken: the first-parent diff was supposed to be empty here"
expect_pass "$UNCHANGED_BASE" "$MERGED" \
  "a merge whose first-parent diff is empty must still pass on its real change"

# 5. Permissions only — zero lines changed, and a real change.
git -C "$repo" checkout -q -B mode "$BASE"
chmod +x "$repo/a.txt"
commit_all "make it executable"
MODE="$(git -C "$repo" rev-parse HEAD)"
[ "$(git -C "$repo" diff --numstat "$BASE" "$MODE" | cut -f1,2)" = "$(printf '0\t0')" ] \
  || self_fail "fixture broken: a mode change was supposed to be zero lines"
expect_pass "$BASE" "$MODE" "a permissions-only change must pass"

# 6. Whitespace only.
git -C "$repo" checkout -q -B space "$BASE"
printf 'base \n' > "$repo/a.txt"
commit_all "trailing space"
SPACE="$(git -C "$repo" rev-parse HEAD)"
expect_pass "$BASE" "$SPACE" "a whitespace-only change must pass"

echo "PASS: check-pr-not-empty self-test (real change, empty, empty after the base moved, merge with an empty first-parent diff, permissions only, whitespace only)"
