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
#       Also decides the change's review tier (A-G) with tiers.py, over the
#       whole PR, and writes it into REVIEW.md and meta. A tier G change is
#       mechanical: no reviewer runs, prepare writes a result.json that says
#       so, and post can be run straight away.
#   .claude/scripts/pr-review.sh post DIR [--dry-run]
#       Check DIR/result.json against the schema and the pinned commit, write
#       the rendered result to DIR/REVIEW-RESULT.md, print it, and remove the
#       review tree. Paste it into the card body; its first line is the one
#       the patrol sweep reads. Makes no GitHub call. --dry-run only prints.
#   .claude/scripts/pr-review.sh show DIR [--line]
#       Print DIR/REVIEW-RESULT.md (--line: only its first line).
#   .claude/scripts/pr-review.sh ready DIR|PR
#       Say whether the PR can be delivered, or what is missing: CI required
#       green on the PR's head, and a review conclusion with no blocker/major
#       on that head. A review of an older commit still counts when the
#       increment since it is one §4 does not re-review (a merge of the base
#       branch, or changes that are only tests, docs or copy); otherwise it
#       says to run an incremental review first. DIR is a prepare directory;
#       PR picks the newest prepare directory for that PR on this machine.
#       Prints 可递 or 还不能递 and the reasons. Exit 0 ready, 1 not ready.
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
  local pr=$1 want=${2:-} since_want=${3:-} sha since="" base tree dir method_rev prev merge_base tier tier_txt
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
    # tiers.py is newer than the rest of the method: while it is only on this
    # branch, main has no copy of it, and refusing to run would take the review
    # tooling down for every other PR. Use this checkout's copy in that window,
    # the same way the whole method falls back below. After it merges, main's
    # copy is the one used and the PR under review cannot loosen its own tier.
    git -C "$ROOT" show "origin/main:$METHOD/tiers.py" > "$dir/tiers.py" 2>/dev/null \
      || { echo "pr-review: tiers.py is not on origin/main yet; using this checkout's copy" >&2
           cp "$ROOT/$METHOD/tiers.py" "$dir/tiers.py" 2>/dev/null || rm -f "$dir/tiers.py"; }
  else
    echo "pr-review: $METHOD is not on origin/main yet; using this checkout's copy" >&2
    method_rev=$(git -C "$ROOT" rev-parse HEAD)
    # The copy is the working tree's: say so when it differs from HEAD, so the
    # comment never names a commit whose method is not the one that ran.
    git -C "$ROOT" diff --quiet HEAD -- "$METHOD" || method_rev="$method_rev+uncommitted"
    cp "$ROOT/$METHOD/prompt.md" "$dir/REVIEW.md"
    cp "$ROOT/$METHOD/schema.json" "$ROOT/$METHOD/render.py" "$dir/"
    [ -f "$ROOT/$METHOD/tiers.py" ] && cp "$ROOT/$METHOD/tiers.py" "$dir/"
  fi

  # The tier is a property of the change, not of the review: always over the PR's
  # whole range, so a delta review still reports the tier of what is being merged.
  # A tier nobody can decide is not worth failing the review over: the criteria
  # stay in REVIEW.md either way, and the conclusion line simply carries none.
  tier=""; tier_txt=""
  if merge_base=$(git -C "$ROOT" merge-base "origin/$base" "$sha" 2>/dev/null) \
    && [ -f "${TIERS:-$dir/tiers.py}" ]; then
    tier=$(python3 "${TIERS:-$dir/tiers.py}" --repo "$ROOT" --base "$merge_base" --head "$sha" --letter 2>/dev/null) || tier=""
    tier_txt=$(python3 "${TIERS:-$dir/tiers.py}" --repo "$ROOT" --base "$merge_base" --head "$sha" 2>/dev/null) || tier_txt=""
  fi
  [ -n "$tier" ] || echo "pr-review: 算不出档位，结论行不带档位" >&2
  [ "$tier" != G ] || echo "pr-review: 档位 G（纯机械改动），不用跑审查；post 会照常出结论行" >&2

  gh api "repos/$REPO/pulls/$pr" -q '"# " + .title + "\n\n" + (.body // "")' > "$dir/pr.md" || exit 1
  printf 'pr=%q\nsha=%q\nbase=%q\nmethod_rev=%q\ntree=%q\nsince=%q\ntier=%q\n' \
    "$pr" "$sha" "$base" "$method_rev" "$tree" "$since" "$tier" > "$dir/meta"
  if [ "$tier" = G ]; then
    # Nothing for a reviewer to do, and the card still needs the conclusion line.
    # The seat runs post as usual. The line says the change was not reviewed, in the
    # summary and again under Not covered, so nobody reads 无 blocker/major as a
    # judgement somebody made by reading.
    python3 - "$sha" "$tier_txt" "$dir/result.json" <<'PY' \
      || die "could not write the tier G result"
import json, sys
sha, tier_txt, out = sys.argv[1:4]
why = next((l.split("：", 1)[1] for l in tier_txt.splitlines()
            if l.startswith("- 为什么会是这一档：")), "")
json.dump({
    "reviewed_sha": sha,
    "summary": f"档位 G：改动全部是{why}，按 §4 免审，CI 过即可。没有审查者读过这些改动，"
               f"这行结论说的是不用审，不是审过之后没发现问题。",
    "candidates_found": 0,
    "candidates_refuted": 0,
    "findings": [],
    "not_covered": [f"整份改动（{why}）都按机械改动免审，没有人读过。"],
}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
    python3 "$dir/render.py" "$dir/result.json" "$sha" "$method_rev" --tier "$tier" >/dev/null \
      || die "the tier G result I wrote does not pass render.py; this is a bug in pr-review.sh"
  fi
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
  [ -z "$tier_txt" ] || cat >> "$dir/REVIEW.md" <<EOF

## Review tier

$tier_txt

档位按改动到的文件定，决定的是该审多深；它不改下面 method 里的步骤，只改你花多少
力气。档位 A 时把两路找茬都用在最危险的那几处上，不要平摊。
EOF
  cat >> "$dir/REVIEW.md" <<EOF

## This review

- PR: #$pr, https://github.com/$REPO/pull/$pr
- Pinned SHA: $sha${since:+ (delta since $since)}
- Base: origin/$base; merge base: $merge_base
- Review tree: $tree
- Changed files: \`git -C $tree diff --name-only ${since:-$merge_base} HEAD\`
- PR title and body (step 5 only): $dir/pr.md
- Schema: $dir/schema.json
- Result: $dir/result.json
- Post: \`$HERE/pr-review.sh post $dir\`
EOF
  echo "$dir/REVIEW.md"
}

post() {
  local dir=$1 dry=${2:-} pr sha base="" method_rev tree since="" tier="" result
  [ -f "$dir/meta" ] || die "$dir has no meta; run prepare first"
  # shellcheck disable=SC1091
  . "$dir/meta"
  # The tier is recomputed here, not read from meta: a seat cannot promote its own
  # change to a lighter tier (or be stuck with a heavier one) by editing the file.
  # A tree that is already gone falls back to what prepare recorded, so post is not
  # broken by posting after a reboot cleared /tmp.
  local range
  if [ -f "$dir/tiers.py" ] \
    && range=$(git -C "$ROOT" merge-base "origin/$base" "$sha" 2>/dev/null) \
    && tier=$(python3 "$dir/tiers.py" --repo "$ROOT" --base "$range" --head "$sha" --letter 2>/dev/null); then
    :
  elif [ -f "$dir/tiers.py" ]; then
    tier=$(python3 "$dir/tiers.py" --repo "$ROOT" --base "$sha^" --head "$sha" --letter 2>/dev/null) || tier=""
  fi
  [ -n "$tier" ] || echo "pr-review: 算不出档位，结论行不带档位" >&2
  result=$(python3 "$dir/render.py" "$dir/result.json" "$sha" "$method_rev" \
    ${since:+--since "$since"} ${tier:+--tier "$tier"}) \
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

ready() {
  local arg=$1 dir
  if [[ $arg =~ ^[0-9]+$ ]]; then
    dir=$(ls -dt "${PR_REVIEW_DIR:-/tmp}"/pr-review-"$arg"-*.input 2>/dev/null | head -n 1)
    [ -n "$dir" ] || die "no prepare directory for #$arg on this machine; run prepare first"
  else
    dir=$arg
  fi
  [ -f "$dir/meta" ] || die "$dir has no meta; that is not a prepare directory"
  # This is the deliverer's own check, so it runs the deliverer's ready.py; nothing about
  # the PR under review can reach it.
  python3 "${READY:-$ROOT/$METHOD/ready.py}" --dir "$dir" --root "$ROOT" --repo "$REPO"
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
  ready) [ $# -eq 2 ] || usage; ready "$2" ;;
  *) usage ;;
esac
