#!/usr/bin/env bash
# One independent review of one PR at one pinned commit, by one method, from
# any agent seat. The seat's own subagents do the reviewing; this script pins
# the commit, hands out the method, and checks and posts the result.
#
#   .claude/scripts/pr-review.sh prepare PR [SHA]
#       Check out SHA (default: the PR's current head) as a detached review
#       tree and write REVIEW.md: the method in .claude/pr-review/prompt.md
#       as it is on origin/main, followed by this review's commit, merge base,
#       paths and post command. Prints the path of REVIEW.md; follow it.
#   .claude/scripts/pr-review.sh post DIR [--dry-run]
#       Check DIR/result.json against the schema and the pinned commit,
#       post it as a PR comment whose first line is
#       `<!-- pr-review sha=SHA -->`, print it, and remove the review tree.
#       --dry-run prints without posting or removing.
#   .claude/scripts/pr-review.sh show PR [SHA]
#       Print the newest review posted for SHA (default: the current head).
#
# Exit status: 0 done, 1 failed (the reason is printed), 2 usage.
set -uo pipefail

REPO=SageSeekerSociety/cheese
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(git -C "$HERE" rev-parse --show-toplevel) || exit 1
METHOD=.claude/pr-review

usage() { sed -n '5,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 2; }
die() { echo "pr-review: $*" >&2; exit 1; }

head_of() { gh api "repos/$REPO/pulls/$1" -q .head.sha; }

# Resolve $2 (full, short or empty) to a full SHA of a commit of PR $1.
resolve_sha() {
  local pr=$1 want=${2:-} commits
  [ -n "$want" ] || { head_of "$pr"; return; }
  commits=$(gh api --paginate "repos/$REPO/pulls/$pr/commits" -q '.[].sha') || return 1
  grep -m1 "^$want" <<<"$commits" || { echo "pr-review: $want is not a commit of #$pr" >&2; return 1; }
}

prepare() {
  local pr=$1 sha base tree dir method_rev
  sha=$(resolve_sha "$pr" "${2:-}") || exit 1
  base=$(gh api "repos/$REPO/pulls/$pr" -q .base.ref) || exit 1
  git -C "$ROOT" fetch -q origin "refs/pull/$pr/head" "$base" main || die "could not fetch #$pr"
  git -C "$ROOT" cat-file -e "$sha^{commit}" 2>/dev/null || die "$sha is not reachable after fetching #$pr"

  tree=${PR_REVIEW_DIR:-/tmp}/pr-review-$pr-${sha:0:12}
  dir=$tree.input
  if [ -e "$tree" ]; then git -C "$ROOT" worktree remove --force "$tree" 2>/dev/null || rm -rf "$tree"; fi
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
  printf 'pr=%q\nsha=%q\nmethod_rev=%q\ntree=%q\n' "$pr" "$sha" "$method_rev" "$tree" > "$dir/meta"
  cat >> "$dir/REVIEW.md" <<EOF

## This review

- PR: #$pr, https://github.com/$REPO/pull/$pr
- Pinned SHA: $sha
- Base: origin/$base; merge base: $(git -C "$ROOT" merge-base "origin/$base" "$sha")
- Review tree: $tree
- Changed files: \`git -C $tree diff --name-only <merge base> HEAD\`
- PR title and body (step 5 only): $dir/pr.md
- Schema: $dir/schema.json
- Result: $dir/result.json
- Post: \`$HERE/pr-review.sh post $dir\`
EOF
  echo "$dir/REVIEW.md"
}

post() {
  local dir=$1 dry=${2:-} pr sha method_rev tree comment head_now
  [ -f "$dir/meta" ] || die "$dir has no meta; run prepare first"
  # shellcheck disable=SC1091
  . "$dir/meta"
  head_now=$(head_of "$pr" 2>/dev/null || true)
  comment=$(python3 "$dir/render.py" "$dir/result.json" "$sha" "$method_rev" "$head_now") \
    || die "$dir/result.json was not posted; fix it and run post again"
  if [ "$dry" = --dry-run ]; then printf '%s\n' "$comment"; return; fi
  gh pr comment "$pr" -R "$REPO" --body "$comment" >/dev/null || die "could not comment on #$pr"
  printf '%s\n' "$comment"
  git -C "$ROOT" worktree remove --force "$tree" 2>/dev/null || true
  echo "pr-review: posted on #$pr; the result stays in $dir" >&2
}

show() {
  local pr=$1 sha id
  sha=$(resolve_sha "$pr" "${2:-}") || exit 1
  id=$(gh api --paginate "repos/$REPO/issues/$pr/comments" \
    --jq ".[] | select(.body | startswith(\"<!-- pr-review sha=$sha -->\")) | .id" | tail -1)
  [ -n "$id" ] || die "no review of #$pr at ${sha:0:12} yet"
  gh api "repos/$REPO/issues/comments/$id" -q .body
}

case "${1:-}" in
  prepare) [ $# -ge 2 ] && [ $# -le 3 ] && [[ $2 =~ ^[0-9]+$ ]] || usage; prepare "$2" "${3:-}" ;;
  post) [ $# -ge 2 ] && [ $# -le 3 ] || usage; post "$2" "${3:-}" ;;
  show) [ $# -ge 2 ] && [ $# -le 3 ] && [[ $2 =~ ^[0-9]+$ ]] || usage; show "$2" "${3:-}" ;;
  *) usage ;;
esac
