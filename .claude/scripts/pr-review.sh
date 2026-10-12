#!/usr/bin/env bash
# Ask for an independent review of one PR at one pinned commit, wait for it,
# and print it.
#
#   .claude/scripts/claude-review.sh [--again] [--no-wait] PR [SHA]
#
# SHA defaults to the PR's current head. The review runs in the `dispatched`
# job of .github/workflows/claude-review.yml on a GitHub-hosted runner, by the
# method in .github/claude-review/prompt.md (pin the SHA, two independent
# finders, each finding refuted by a third party, evidence graded by rung),
# and lands as a PR comment whose first line is `<!-- claude-review sha=SHA -->`.
# Every seat gets the same reviewer with the same instructions, whatever
# harness it runs in.
#
#   --again    review even if a comment for this SHA already exists (by
#              default the existing one is printed: same commit, same method)
#   --no-wait  send the request and exit; run again later to print the result
#
# Exit status: 0 the review is printed, 1 the review failed (the run link is
# printed), 2 usage, 3 still waiting after CLAUDE_REVIEW_TIMEOUT seconds
# (default 3000). A review takes 5-20 minutes; run this in the background.
set -uo pipefail

REPO=SageSeekerSociety/cheese
TIMEOUT=${CLAUDE_REVIEW_TIMEOUT:-3000}
INTERVAL=30
again=0 wait=1 args=()
for a in "$@"; do
  case "$a" in
    --again) again=1 ;;
    --no-wait) wait=0 ;;
    -*) args=(); break ;;
    *) args+=("$a") ;;
  esac
done
usage() { echo "usage: $0 [--again] [--no-wait] PR [SHA]" >&2; exit 2; }
[ ${#args[@]} -ge 1 ] && [ ${#args[@]} -le 2 ] || usage
pr=${args[0]}
case "$pr" in '' | *[!0-9]*) usage ;; esac

sha=${args[1]:-$(gh pr view "$pr" -R "$REPO" --json headRefOid -q .headRefOid)} || exit 1
# A short SHA would not match the marker; resolve it against the PR's commits.
if ! echo "$sha" | grep -qxE '[0-9a-f]{40}'; then
  full=$(gh api --paginate "repos/$REPO/pulls/$pr/commits" -q '.[].sha' | grep "^$sha" | head -1)
  [ -n "$full" ] || { echo "$sha is not a commit of #$pr" >&2; exit 2; }
  sha=$full
fi

# The newest comment on the PR carrying this SHA's marker: "ok <body>" or
# "failed <body>", nothing when there is none yet.
comment_for() {
  gh api --paginate "repos/$REPO/issues/$pr/comments" \
    --jq ".[] | select(.body | startswith(\"<!-- claude-review sha=$sha\")) | select(.created_at >= \"$1\") | .body" \
    | awk -v sha="$sha" '
        index($0, "<!-- claude-review sha=" sha " failed -->") == 1 { if (body != "") last = body; body = "failed\n"; next }
        index($0, "<!-- claude-review sha=" sha " -->") == 1 { if (body != "") last = body; body = "ok\n"; next }
        { body = body $0 "\n" }
        END { if (body != "") last = body; printf "%s", last }'
}

print_result() { # $1 = output of comment_for
  local status=${1%%$'\n'*} body=${1#*$'\n'}
  printf '%s' "$body"
  [ "$status" = ok ]
}

title="Claude review #$pr @ $sha"
# The newest run of this review created at or after $1:
# "<id> <status> <conclusion> <createdAt>", nothing when there is none.
run_for() {
  gh run list -R "$REPO" --workflow claude-review.yml --event repository_dispatch --limit 50 \
    --json databaseId,displayTitle,status,conclusion,createdAt \
    -q "[.[] | select(.displayTitle == \"$title\" and .createdAt >= \"$1\")][0] // empty
        | \"\(.databaseId) \(.status) \(.conclusion) \(.createdAt)\"" 2>/dev/null
}

since=
if [ "$again" = 0 ]; then
  existing=$(comment_for "1970-01-01T00:00:00Z")
  if [ "${existing%%$'\n'*}" = ok ]; then
    echo "# #$pr @ ${sha:0:12} was already reviewed; --again to review it anew" >&2
    print_result "$existing"; exit
  fi
  # A review asked for earlier (say with --no-wait) and still running: wait
  # for that one instead of paying for a second.
  read -r id status _ created < <(run_for "1970-01-01T00:00:00Z")
  if [ -n "${id:-}" ] && [ "$status" != completed ]; then
    since=$created
    echo "# a review of #$pr @ ${sha:0:12} is already running: https://github.com/$REPO/actions/runs/$id" >&2
  fi
fi

if [ -z "$since" ]; then
  since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  gh api "repos/$REPO/dispatches" -X POST -f event_type=claude-review \
    -f "client_payload[pr]=$pr" -f "client_payload[sha]=$sha" >/dev/null || exit 1
  echo "# asked for a review of #$pr @ ${sha:0:12} at $since" >&2
fi
[ "$wait" = 1 ] || exit 0

deadline=$(( $(date +%s) + TIMEOUT ))
run_url=
while [ "$(date +%s)" -lt "$deadline" ]; do
  sleep "$INTERVAL"
  result=$(comment_for "$since")
  if [ -n "$result" ]; then print_result "$result"; exit; fi
  # No comment yet. A run that ended without posting one (it failed before
  # the PR check passed, or was cancelled) would leave us waiting forever.
  read -r id status conclusion _ < <(run_for "$since")
  if [ -n "${id:-}" ]; then
    run_url="https://github.com/$REPO/actions/runs/$id"
    if [ "$status" = completed ]; then
      sleep 5
      result=$(comment_for "$since")
      if [ -n "$result" ]; then print_result "$result"; exit; fi
      echo "review run ended ($conclusion) without a result: $run_url" >&2
      exit 1
    fi
  fi
done
echo "still waiting after ${TIMEOUT}s${run_url:+: $run_url}; run this again to print the result once it lands" >&2
exit 3
