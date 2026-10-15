#!/usr/bin/env bash
# Watch pull requests until each reaches an end state, printing one line per
# change, so whoever runs it (a person, or an agent under a monitor that turns
# each line into a notification) hears about every step without asking.
#
#   .claude/scripts/pr-watch.sh [--enqueue] [--deploy] PR...
#
#   --enqueue  put a PR into the merge queue once `CI required` passes and it is
#              in no queue and has no auto-merge armed. Also re-enqueues a PR the
#              queue removed for a reason other than a conflict or a failed
#              check, at most once per PR.
#   --deploy   after a PR merges, follow the dev deploy (deploy-dev.yml) of a
#              main commit that contains it, and report its result.
#
# End states, each printed and then no longer polled: merged (and, with
# --deploy, deployed or deploy failed), closed, CI failed, conflicting with its
# base branch, removed from the queue without being re-enqueued. The script
# exits once every PR has ended; its exit status is 0 only if every PR merged
# (and deployed, with --deploy). A PR whose state has not changed for
# PR_WATCH_QUIET seconds (default 3600) gets a line saying where it is waiting,
# so a state this script does not recognise still reaches whoever watches.
#
# Lines look like `HH:MM:SSZ #2847 queue: AWAITING_CHECKS (position 3)`. Run it
# under a monitor, which turns each line into a notification; with its output
# going to a file or a background shell, a session hears only when it exits.
# It never prompts.
set -uo pipefail

REPO=SageSeekerSociety/cheese
DEV_VERSION_URL=https://okcheese.com/api/version
INTERVAL=${PR_WATCH_INTERVAL:-60}
QUIET=${PR_WATCH_QUIET:-3600}
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

declare -A last ended ok requeued spoke
say() { echo "$(date -u +%H:%M:%SZ) #$1 $2"; spoke[$1]=$(date +%s); }
quiet() { # $1 PR, $2 where it is waiting; speaks only after QUIET seconds of silence
  [ $(( $(date +%s) - ${spoke[$1]:-0} )) -ge "$QUIET" ] && say "$1" "no change in $((QUIET / 60)) min: $2"
}
started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

query='query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p){
  state mergeable mergeStateStatus autoMergeRequest{enabledAt} mergeCommit{oid} mergeQueueEntry{state position}
  timelineItems(last:1,itemTypes:[REMOVED_FROM_MERGE_QUEUE_EVENT]){nodes{... on RemovedFromMergeQueueEvent{createdAt reason}}}}}}'

ci_required() { # pass | fail | pending | none
  gh pr checks "$1" -R "$REPO" 2>/dev/null | awk -F'\t' '$1=="CI required"{print $2; f=1} END{if(!f) print "none"}'
}

# The machine this runs on has no `jq`, and its absence used to cost the whole
# script every field it polls: a round of `jq: command not found`, nothing
# parsed, `ci_required()` reading an empty state forever, and a watcher that
# looks like it is watching while taking no action at all. `gh` carries a jq of
# its own, but the reply is in hand already and python3 is on the same machine
# as the script — so the fields come out of that, one line.
#
# `|` and not a tab between them: a tab is IFS whitespace, so bash collapses a
# run of them and every empty field shifts the ones after it. No field can hold
# a `|` — they are GitHub's own enums, a SHA, and the ISO timestamp of a removal.
pr_fields() { # state, merge commit, queue, removal, mergeable, merge state, auto-merge
  python3 -c 'import json,sys
p=json.load(sys.stdin)["data"]["repository"]["pullRequest"]
e=p.get("mergeQueueEntry") or {}
nodes=(p.get("timelineItems") or {}).get("nodes") or []
r=(nodes[0] if nodes else None) or {}
mc=p.get("mergeCommit") or {}
queue=(str(e["state"])+" "+str(e["position"])) if e else ""
removed=(str(r["createdAt"])+" "+str(r["reason"])) if r else ""
print("|".join([p.get("state") or "", mc.get("oid") or "", queue, removed,
                p.get("mergeable") or "", p.get("mergeStateStatus") or "",
                "auto-merge on" if p.get("autoMergeRequest") else ""]))'
}

live_sha() { # the SHA the version endpoint reports, or nothing
  python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("data",{}).get("sha") or "", end="")
except Exception: pass'
}

deploy_of() { # "<status> <conclusion> <run id>" of the newest dev deploy containing $1, or nothing
  local merge=$1 id st co sha
  # Every field is a word, a run still going included (its conclusion is
  # empty, printed as none): `read` would otherwise shift the commit into co.
  while read -r id st co sha; do
    # A run's headSha is main's tip when the build-completed event fired, not
    # the commit it releases: a deploy of an earlier build, cancelled when a
    # newer one took its place in the deploy-dev group, carries this merge's
    # SHA there. The commit it releases is the one its run name carries.
    [ "$sha" != - ] || continue
    gh api "repos/$REPO/compare/$merge...$sha" -q '.status' 2>/dev/null | grep -qE '^(ahead|identical)$' || continue
    # A run whose eligibility check refused the release fails with its deploy
    # job skipped; that run shipped nothing, so it is not the deploy.
    [ "$(gh run view "$id" -R "$REPO" --json jobs -q '.jobs[]|select(.name=="deploy")|.conclusion' 2>/dev/null)" = skipped ] && continue
    echo "$st $co $id"; return
  done < <(gh run list -R "$REPO" --workflow deploy-dev.yml --limit 8 \
             --json databaseId,status,conclusion,displayTitle \
             -q '.[]|"\(.databaseId) \(.status) \(.conclusion // "" | if . == "" then "none" else . end) \([.displayTitle|scan("[0-9a-f]{40}")][0] // "-")"')
}

while :; do
  open=0
  for p in "${prs[@]}"; do
    [ -n "${ended[$p]:-}" ] && continue
    open=1
    j=$(gh api graphql -f query="$query" -F o="${REPO%/*}" -F n="${REPO#*/}" -F p="$p" 2>/dev/null) || continue
    IFS='|' read -r state merge queue removed mergeable mstate auto <<<"$(printf '%s' "$j" | pr_fields)"

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
          live=$(curl -fsS "$DEV_VERSION_URL" 2>/dev/null | live_sha)
          if [ -n "$live" ] && gh api "repos/$REPO/compare/$merge...$live" -q '.status' 2>/dev/null | grep -qE '^(ahead|identical)$'; then
            say "$p" "live on dev at ${live:0:9}"; ended[$p]=1 ok[$p]=1
          elif [ "${last[$p]:-}" != "$key live" ]; then
            say "$p" "deploy run ${d##* } succeeded but dev runs ${live:0:9}, which lacks it; waiting"
            last[$p]="$key live"
          else
            quiet "$p" "merged; dev runs ${live:0:9}, which lacks it"
          fi ;;
        completed\ *) ended[$p]=1 ;;
        "") quiet "$p" "merged; no dev deploy contains it yet" ;;
        *) quiet "$p" "merged; dev deploy run ${d##* }: ${d% *}" ;;
      esac
      continue
    fi
    if [ "$state" = CLOSED ]; then say "$p" "closed without merging"; ended[$p]=1; continue; fi

    # GitHub queues nothing that conflicts with its base, and the PR shows no
    # other change while it waits for a rebase that only its author can make.
    if [ "$mergeable" = CONFLICTING ]; then
      say "$p" "stopped watching: conflicts with its base branch, so it cannot enter the queue"
      ended[$p]=1; continue
    fi

    ci=$(ci_required "$p")
    key="$ci|${queue% *}|$removed|$auto"   # the position alone changing is not news
    if [ "${last[$p]:-}" = "$key" ]; then
      where="not in the queue"; [ -n "$queue" ] && where="queue: ${queue% *}"
      quiet "$p" "CI required: $ci; $where${auto:+, $auto}; merge state $mstate"
      continue
    fi
    prev=${last[$p]:-}; last[$p]=$key
    [ "${prev%%|*}" != "$ci" ] && say "$p" "CI required: $ci"
    if [ -n "$queue" ]; then
      say "$p" "queue: ${queue% *} (position ${queue##* })"
    elif [ -n "$removed" ] && [[ "${removed%% *}" > "$started" ]] && [ "$(cut -d'|' -f3 <<<"$prev")" != "$removed" ]; then
      reason=${removed#* }
      say "$p" "removed from the merge queue: $reason"
      if [ "$enqueue" = 1 ] && [ -z "${requeued[$p]:-}" ] && [ "$ci" = pass ] \
         && ! grep -qiE 'merge_conflict|failed_checks' <<<"$reason"; then
        requeued[$p]=1
        gh pr merge "$p" -R "$REPO" >/dev/null 2>&1 && say "$p" "re-enqueue requested once" || say "$p" "re-enqueue failed"
        continue
      fi
      ended[$p]=1; continue
    fi
    if [ "$ci" = fail ]; then say "$p" "stopped watching: CI required failed"; ended[$p]=1; continue; fi
    [ -n "$auto" ] && say "$p" "not in the queue; $auto, merge state $mstate"
    # `gh pr merge` succeeds both when it queues the PR and when it only arms
    # auto-merge, so what it did is reported from the next poll, not from here.
    if [ "$enqueue" = 1 ] && [ "$ci" = pass ] && [ -z "$queue" ] && [ -z "$auto" ]; then
      gh pr merge "$p" -R "$REPO" >/dev/null 2>&1 && say "$p" "enqueue requested" || say "$p" "enqueue failed"
    fi
  done
  [ "$open" = 1 ] || break
  sleep "$INTERVAL"
done

status=0
for p in "${prs[@]}"; do [ -n "${ok[$p]:-}" ] || status=1; done
echo "$(date -u +%H:%M:%SZ) done: ${#prs[@]} PR(s), exit $status"
exit $status
