#!/usr/bin/env bash
# One independent review of one PR at one pinned commit, by one method, from
# any agent seat. The seat's own subagents do the reviewing; this script pins
# the commit, hands out the method, and checks and renders the result. Nothing
# is posted to GitHub: the result goes into the acceptance card body.
#
#   .claude/scripts/pr-review.sh prepare PR [SHA] [--since REVIEWED_SHA]
#       Check out SHA (default: the PR's current head) as a detached review
#       tree and write REVIEW.md: the method in .claude/pr-review/prompt.md
#       as it is on origin/main, followed by this review's commit, merge base,
#       paths and post command. Prints the path of REVIEW.md; follow it.
#       --since reviews only REVIEWED_SHA..SHA, to check that the last
#       round's blocker/major findings are fixed.
#   .claude/scripts/pr-review.sh post DIR [--dry-run]
#       Check DIR/result.json against the schema and the pinned commit, write
#       the rendered result to DIR/REVIEW-RESULT.md, print it, and remove the
#       review tree. Paste it into the card body; its first line is the one
#       the patrol sweep reads. Makes no GitHub call. --dry-run only prints.
#   .claude/scripts/pr-review.sh show DIR [--line]
#       Print DIR/REVIEW-RESULT.md (--line: only its first line).
#
# Exit status: 0 done, 1 failed (the reason is printed), 2 usage.
set -uo pipefail

REPO=SageSeekerSociety/cheese
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
METHOD=.claude/pr-review

usage() { sed -n '/^#   \.claude/,/^# Exit status/p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }
die() { echo "pr-review: $*" >&2; exit 1; }

head_of() { gh api "repos/$REPO/pulls/$1" -q .head.sha; }

# Resolve $2 (full, short or empty) to a full SHA of a commit of PR $1.
resolve_sha() {
  local pr=$1 want=${2:-} commits
  [ -n "$want" ] || { head_of "$pr"; return; }
  [[ $want =~ ^[0-9a-f]{4,40}$ ]] || { echo "pr-review: $want is not a commit SHA" >&2; return 1; }
  commits=$(gh api --paginate "repos/$REPO/pulls/$pr/commits" -q '.[].sha') || return 1
  grep -m1 "^$want" <<<"$commits" || { echo "pr-review: $want is not a commit of #$pr" >&2; return 1; }
}

prepare() {
  local pr=$1 want=${2:-} since_want=${3:-} sha since="" base tree dir method_rev from prev
  sha=$(resolve_sha "$pr" "$want") || exit 1
  [ -z "$since_want" ] || since=$(resolve_sha "$pr" "$since_want") || exit 1
  base=$(gh api "repos/$REPO/pulls/$pr" -q .base.ref) || exit 1
  git -C "$ROOT" fetch -q origin "refs/pull/$pr/head" "$base" main || die "could not fetch #$pr"
  git -C "$ROOT" cat-file -e "$sha^{commit}" 2>/dev/null || die "$sha is not reachable after fetching #$pr"
  if [ -n "$since" ]; then
    git -C "$ROOT" merge-base --is-ancestor "$since" "$sha" 2>/dev/null \
      || die "${since:0:12} is not an ancestor of ${sha:0:12} (rebased or force-pushed?); review the whole PR instead"
    [ "$since" != "$sha" ] || die "${since:0:12} is already the head; nothing to review since"
  fi

  tree=${PR_REVIEW_DIR:-/tmp}/pr-review-$pr-${sha:0:12}
  dir=$tree.input
  # Unconditionally: a tree whose directory is gone (a reboot cleared /tmp)
  # still holds its registration, and that alone blocks a second add.
  git -C "$ROOT" worktree remove --force "$tree" 2>/dev/null; rm -rf "$tree"
  rm -rf "$dir"; mkdir -p "$dir"
  git -C "$ROOT" worktree add -q --detach "$tree" "$sha" || die "could not check out $sha"

  # The method as merged on the default branch; the PR cannot rewrite it.
  # Until this script itself is on main, fall back to the checkout's copy.
  if method_rev=$(git -C "$ROOT" rev-parse -q --verify "origin/main:$METHOD/prompt.md" 2>/dev/null); then
    method_rev=$(git -C "$ROOT" log -1 --format=%H origin/main -- "$METHOD")
    git -C "$ROOT" show "origin/main:$METHOD/prompt.md" > "$dir/REVIEW.md"
    git -C "$ROOT" show "origin/main:$METHOD/schema.json" > "$dir/schema.json"
    git -C "$ROOT" show "origin/main:$METHOD/render.py" > "$dir/render.py"
  else
    echo "pr-review: $METHOD is not on origin/main yet; using this checkout's copy" >&2
    method_rev=$(git -C "$ROOT" rev-parse HEAD)
    # The copy is the working tree's: say so when it differs from HEAD, so the
    # comment never names a commit whose method is not the one that ran.
    git -C "$ROOT" diff --quiet HEAD -- "$METHOD" || method_rev="$method_rev+uncommitted"
    cp "$ROOT/$METHOD/prompt.md" "$dir/REVIEW.md"
    cp "$ROOT/$METHOD/schema.json" "$ROOT/$METHOD/render.py" "$dir/"
  fi

  gh api "repos/$REPO/pulls/$pr" -q '"# " + .title + "\n\n" + (.body // "")' > "$dir/pr.md" || exit 1
  printf 'pr=%q\nsha=%q\nmethod_rev=%q\ntree=%q\nsince=%q\n' "$pr" "$sha" "$method_rev" "$tree" "$since" > "$dir/meta"
  if [ -n "$since" ]; then
    # The last round's own result, when this machine still has it; otherwise
    # its findings are in the PR body (pr.md), where the card body is mirrored.
    prev=${PR_REVIEW_DIR:-/tmp}/pr-review-$pr-${since:0:12}.input/REVIEW-RESULT.md
    if [ -f "$prev" ]; then cp "$prev" "$dir/previous.md"; else rm -f "$dir/previous.md"; fi
    cat >> "$dir/REVIEW.md" <<EOF

## Incremental review

This is a delta review of \`${since:0:12}..${sha:0:12}\`, not of the whole PR. It
changes the scope of steps 1 to 3 as follows; steps 4 to 7 stand.

- The previous round reviewed ${since:0:12}. Its blocker and major findings
  are in $dir/previous.md if that file exists, otherwise in the review
  section at the top of the PR body (pr.md): read only that section, now.
- The change under review is \`git diff ${since:0:12} HEAD\`. Commits that
  arrived by merging the base branch are not the author's change; list the
  author's with \`git log --no-merges ${since:0:12}..HEAD --not origin/$base\`.
- Finder A checks, for each blocker and major finding of the last round,
  whether the failure is gone at ${sha:0:12}: re-run its confirm command or
  replay its failure. Finder B looks at what the increment adds, with the
  lenses of step 2, only inside the increment.
- Refute as in step 3. Findings about code outside the increment are out of
  scope, even if they were missed last time.
- The result's summary says, finding by finding, which of the last round's
  blockers and majors are fixed, and which are not.
EOF
  fi
  cat >> "$dir/REVIEW.md" <<EOF

## This review

- PR: #$pr, https://github.com/$REPO/pull/$pr
- Pinned SHA: $sha${since:+ (delta since $since)}
- Base: origin/$base; merge base: $(git -C "$ROOT" merge-base "origin/$base" "$sha")
- Review tree: $tree
- Changed files: \`git -C $tree diff --name-only ${since:-<merge base>} HEAD\`
- PR title and body (step 5 only): $dir/pr.md
- Schema: $dir/schema.json
- Result: $dir/result.json
- Post: \`$HERE/pr-review.sh post $dir\`
EOF
  echo "$dir/REVIEW.md"
}

post() {
  local dir=$1 dry=${2:-} pr sha method_rev tree since="" result
  [ -f "$dir/meta" ] || die "$dir has no meta; run prepare first"
  # shellcheck disable=SC1091
  . "$dir/meta"
  result=$(python3 "$dir/render.py" "$dir/result.json" "$sha" "$method_rev" ${since:+--since "$since"}) \
    || die "$dir/result.json was not accepted; fix it and run post again"
  printf '%s\n' "$result"
  [ "$dry" = --dry-run ] && return
  printf '%s\n' "$result" > "$dir/REVIEW-RESULT.md"
  git -C "$ROOT" worktree remove --force "$tree" 2>/dev/null || true
  echo "pr-review: wrote $dir/REVIEW-RESULT.md; paste it into the card body (nothing was posted)" >&2
}

show() {
  local file=$1/REVIEW-RESULT.md
  [ -f "$file" ] || die "$file does not exist; run post first"
  if [ "${2:-}" = --line ]; then head -n 1 "$file"; else cat "$file"; fi
}

case "${1:-}" in
  prepare)
    [ $# -ge 2 ] && [[ $2 =~ ^[0-9]+$ ]] || usage
    pr=$2; shift 2; want=""; since=""
    if [ $# -gt 0 ] && [[ $1 != -* ]]; then want=$1; shift; fi
    if [ $# -eq 2 ] && [ "$1" = --since ]; then since=$2; shift 2; fi
    [ $# -eq 0 ] || usage
    prepare "$pr" "$want" "$since" ;;
  post) [ $# -ge 2 ] && [ $# -le 3 ] && { [ $# -eq 2 ] || [ "$3" = --dry-run ]; } || usage; post "$2" "${3:-}" ;;
  show) [ $# -ge 2 ] && [ $# -le 3 ] && { [ $# -eq 2 ] || [ "$3" = --line ]; } || usage; show "$2" "${3:-}" ;;
  *) usage ;;
esac
