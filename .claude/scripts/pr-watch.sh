#!/usr/bin/env bash
# Watch pull requests until each reaches an end state, printing one line per
# change, so whoever runs it (a person, or an agent under a monitor that turns
# each line into a notification) hears about every step without asking.
#
#   .claude/scripts/pr-watch.sh [--enqueue] [--deploy] PR...
#
#   --enqueue  put a PR into the merge queue once `CI required` passes and it is
#              in no queue. Also re-enqueues a PR the queue removed for a reason
#              other than a conflict or a failed check, at most once per PR.
#   --deploy   after a PR merges, follow the dev deploy (deploy-dev.yml) of a
#              main commit that contains it, and report its result.
#
# End states, each printed and then no longer polled: merged (and, with
# --deploy, deployed or deploy failed), closed, CI failed, removed from the
# queue without being re-enqueued. The script exits once every PR has ended;
# its exit status is 0 only if every PR merged (and deployed, with --deploy).
#
# Lines look like `HH:MM:SSZ #2847 queue: added (position 3)`. Run it under a
# monitor or in the background; it never prompts.
set -uo pipefail

REPO=SageSeekerSociety/cheese
DEV_VERSION_URL=https://okcheese.com/api/version
INTERVAL=${PR_WATCH_INTERVAL:-60}
enqueue=0 deploy=0 prs=()
for a in "$@"; do
  case "$a" in
    --enqueue) enqueue=1 ;;
    --deploy) deploy=1 ;;
    [0-9]*) prs+=("$a") ;;
    *) echo "usage: $0 [--enqueue] [--deploy] PR..." >&2; exit 2 ;;
  esac
done
[ ${#prs[@]} -gt 0 ] || { echo "usage: $0 [--enqueue] [--deploy] PR..." >&2; exit 2; }

say() { echo "$(date -u +%H:%M:%SZ) #$1 $2"; }
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

declare -A last ended ok requeued
query='query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p){
  state mergeCommit{oid} mergeQueueEntry{state position}
  timelineItems(last:1,itemTypes:[REMOVED_FROM_MERGE_QUEUE_EVENT]){nodes{... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}'

ci_required() { # pass | fail | pending | none
  gh pr checks "$1" -R "$REPO" 2>/dev/null | awk -F'\t' '$1=="CI required"{print $2; f=1} END{if(!f) print "none"}'
}

deploy_of() { # "<status> <conclusion> <run id>" of the newest dev deploy containing $1, or nothing
  local merge=$1 id st co sha
  while read -r id st co sha; do
    gh api "repos/$REPO/compare/$merge...$sha" -q '.status' 2>/dev/null | grep -qE '^(ahead|identical)$' || continue
    # A run whose eligibility check found nothing to release concludes success
    # with its deploy job skipped; that run shipped nothing, so it is not the deploy.
    [ "$(gh run view "$id" -R "$REPO" --json jobs -q '.jobs[]|select(.name=="deploy")|.conclusion' 2>/dev/null)" = skipped ] && continue
    echo "$st ${co:-none} $id"; return
  done < <(gh run list -R "$REPO" --workflow deploy-dev.yml --limit 8 \
             --json databaseId,status,conclusion,headSha -q '.[]|"\(.databaseId) \(.status) \(.conclusion) \(.headSha)"')
}

while :; do
  open=0
  for p in "${prs[@]}"; do
    [ -n "${ended[$p]:-}" ] && continue
    open=1
    j=$(gh api graphql -f query="$query" -F o="${REPO%/*}" -F n="${REPO#*/}" -F p="$p" 2>/dev/null) || continue
    state=$(jq -r '.data.repository.pullRequest.state' <<<"$j")
    merge=$(jq -r '.data.repository.pullRequest.mergeCommit.oid // ""' <<<"$j")
    queue=$(jq -r '.data.repository.pullRequest.mergeQueueEntry | if . then "\(.state) \(.position)" else "" end' <<<"$j")
    removed=$(jq -r '.data.repository.pullRequest.timelineItems.nodes[0] | if . then "\(.createdAt) \(.reason)" else "" end' <<<"$j")

    if [ "$state" = MERGED ]; then
      if [ "$deploy" = 0 ]; then
        [ "${last[$p]:-}" != merged ] && say "$p" "merged as ${merge:0:9}"
        ended[$p]=1 ok[$p]=1; continue
      fi
      d=$(deploy_of "$merge"); key="merged ${d% *}"
      if [ "${last[$p]:-}" != "$key" ]; then
        [ -z "$d" ] && say "$p" "merged as ${merge:0:9}; waiting for a dev deploy that contains it" \
                    || say "$p" "dev deploy run ${d##* }: ${d% *}"
        last[$p]=$key
      fi
      case "$d" in
        "completed success "*)
          # A run can succeed while shipping an older commit whose images were
          # the newest built, so deployed means the live version contains it.
          live=$(curl -fsS "$DEV_VERSION_URL" 2>/dev/null | jq -r '.data.sha // empty')
          if [ -n "$live" ] && gh api "repos/$REPO/compare/$merge...$live" -q '.status' 2>/dev/null | grep -qE '^(ahead|identical)$'; then
            say "$p" "live on dev at ${live:0:9}"; ended[$p]=1 ok[$p]=1
          elif [ "${last[$p]:-}" != "$key live" ]; then
            say "$p" "deploy run ${d##* } succeeded but dev runs ${live:0:9}, which lacks it; waiting"
            last[$p]="$key live"
          fi ;;
        completed\ *) ended[$p]=1 ;;
      esac
      continue
    fi
    if [ "$state" = CLOSED ]; then say "$p" "closed without merging"; ended[$p]=1; continue; fi

    ci=$(ci_required "$p")
    key="$ci|${queue% *}|$removed"   # the position alone changing is not news
    [ "${last[$p]:-}" = "$key" ] && continue
    prev=${last[$p]:-}; last[$p]=$key
    [ "${prev%%|*}" != "$ci" ] && say "$p" "CI required: $ci"
    if [ -n "$queue" ]; then
      say "$p" "queue: ${queue% *} (position ${queue##* })"
    elif [ -n "$removed" ] && [[ "${removed%% *}" > "$started" ]] && [ "$(cut -d'|' -f3 <<<"$prev")" != "$removed" ]; then
      reason=${removed#* }
      say "$p" "removed from the merge queue: $reason"
      if [ "$enqueue" = 1 ] && [ -z "${requeued[$p]:-}" ] && [ "$ci" = pass ] \
         && ! grep -qE 'MERGE_CONFLICT|FAILED_CHECKS' <<<"$reason"; then
        requeued[$p]=1
        gh pr merge "$p" -R "$REPO" >/dev/null 2>&1 && say "$p" "re-enqueued once" || say "$p" "re-enqueue failed"
        continue
      fi
      ended[$p]=1; continue
    fi
    if [ "$ci" = fail ]; then say "$p" "stopped watching: CI required failed"; ended[$p]=1; continue; fi
    if [ "$enqueue" = 1 ] && [ "$ci" = pass ] && [ -z "$queue" ]; then
      gh pr merge "$p" -R "$REPO" >/dev/null 2>&1 && say "$p" "enqueued" || say "$p" "enqueue failed"
    fi
  done
  [ "$open" = 1 ] || break
  sleep "$INTERVAL"
done

status=0
for p in "${prs[@]}"; do [ -n "${ok[$p]:-}" ] || status=1; done
echo "$(date -u +%H:%M:%SZ) done: ${#prs[@]} PR(s), exit $status"
exit $status
