#!/usr/bin/env bash
# Watch pull requests until each reaches an end state, printing one line per
# change, so whoever runs it (a person, or an agent under a monitor that turns
# each line into a notification) hears about every step without asking.
#
#   .claude/scripts/pr-watch.sh [--enqueue] [--deploy] PR...
#
#   --enqueue  squash-merge once `pr-review.sh ready` prints 可递 for the current
#              head. Re-read the head before merging and pin the mutation to it.
#              Never arm auto-merge. Without this flag, observe only.
#   After merging, follow Required CI's push-to-main runs of main commits that
#   contain the merge: passed as soon as any of them succeeded. A red run is not
#   final while a newer containing batch may still fix main; the watch fails
#   once the newest containing run is red and nothing newer starts within
#   PR_WATCH_MAIN_RED_WAIT seconds (default 3600). A merge reverted before a
#   green batch or the live version fails the watch, naming the revert.
#   --deploy   after a PR merges, follow the dev deploys (deploy-dev.yml) of
#              main commits that contain it: deployed as soon as any of them
#              succeeded, otherwise the newest one's result.
#
# End states, each printed and then no longer polled: main CI passed (and, with
# --deploy, deployed or deploy failed), closed, CI failed, conflicting with its
# base branch. The script
# exits once every PR has ended; its exit status is 0 only if every PR merged
# (and deployed, with --deploy). A PR whose state has not changed for
# PR_WATCH_QUIET seconds (default 3600) gets a line saying where it is waiting,
# so a state this script does not recognise still reaches whoever watches.
#
# Lines look like `HH:MM:SSZ #2847 main Required CI run 123: completed success`. Run it
# under a monitor, which turns each line into a notification; with its output
# going to a file or a background shell, a session hears only when it exits.
# It never prompts.
set -uo pipefail

REPO=SageSeekerSociety/cheese
DEV_VERSION_URL=https://okcheese.com/api/version
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INTERVAL=${PR_WATCH_INTERVAL:-60}
QUIET=${PR_WATCH_QUIET:-3600}
# Main goes red after a merge mostly because of another PR in the same batch,
# and the batch after it usually fixes main within half an hour; an hour with no
# newer batch means nobody is fixing it, so the watch gives up then.
MAIN_RED_WAIT=${PR_WATCH_MAIN_RED_WAIT:-3600}
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

declare -A last ended ok spoke main_last red_run red_since
say() { echo "$(date -u +%H:%M:%SZ) #$1 $2"; spoke[$1]=$(date +%s); }
quiet() { # $1 PR, $2 where it is waiting; speaks only after QUIET seconds of silence
  [ $(( $(date +%s) - ${spoke[$1]:-0} )) -ge "$QUIET" ] && say "$1" "no change in $((QUIET / 60)) min: $2"
}
query='query($o:String!,$n:String!,$p:Int!){repository(owner:$o,name:$n){pullRequest(number:$p){
  state headRefOid mergeable mergeStateStatus autoMergeRequest{enabledAt} mergeCommit{oid}}}}'

ci_required() { # pass | fail | pending | none
  gh pr checks "$1" -R "$REPO" 2>/dev/null | awk -F'\t' '$1=="CI required"{print $2; f=1} END{if(!f) print "none"}'
}

# Python is available where the watcher runs; no standalone jq is required.
# Pipe separators preserve empty fields, unlike tab-delimited bash read.
pr_fields() { # state, head, merge commit, mergeable, merge state, auto-merge
  python3 -c 'import json,sys
p=json.load(sys.stdin)["data"]["repository"]["pullRequest"]
mc=p.get("mergeCommit") or {}
print("|".join([p.get("state") or "", p.get("headRefOid") or "", mc.get("oid") or "",
                p.get("mergeable") or "", p.get("mergeStateStatus") or "",
                "auto-merge on" if p.get("autoMergeRequest") else ""]))'
}

live_sha() { # the SHA the version endpoint reports, or nothing
  python3 -c 'import json,sys
try: print(json.load(sys.stdin).get("data",{}).get("sha") or "", end="")
except Exception: pass'
}

review_ready() { # prints what `pr-review.sh ready` says; exit 0 only for 可递
  "$HERE/pr-review.sh" ready "$1" 2>&1
}

pr_json() {
  gh api graphql -f query="$query" -F o="${REPO%/*}" -F n="${REPO#*/}" -F p="$1" 2>/dev/null
}

contains() {
  gh api "repos/$REPO/compare/$1...$2" -q '.status' 2>/dev/null | grep -qE '^(ahead|identical)$'
}

# The commit between merge $1 and $2 that reverts the merge of PR $3, or
# nothing. Ancestry alone says $2 contains the merge even after a revert, so a
# green batch or a live version past a revert would otherwise count as this
# PR's. Recognised: git revert's "This reverts commit <sha>", GitHub's revert
# button ("Reverts <repo>#<PR>"), and a `Revert "<merge subject>` headline.
reverter_of() {
  local merge=$1 sha=$2 pr=$3 subject csha msg
  [ "$sha" != "$merge" ] || return 0
  subject=$(gh api "repos/$REPO/commits/$merge" -q '.commit.message|split("\n")[0]' 2>/dev/null)
  while IFS=$'\t' read -r csha msg; do
    if [[ "$msg" == *"This reverts commit ${merge:0:7}"* || "$msg" == *"Reverts $REPO#$pr"* ]] \
       || { [ -n "$subject" ] && [[ "$msg" == "Revert \"$subject"* ]]; }; then
      echo "$csha"; return
    fi
  done < <(gh api "repos/$REPO/compare/$merge...$sha" -q '.commits[]|[.sha, .commit.message]|@tsv' 2>/dev/null)
}

# "<status> <conclusion> <run id>" of main's Required CI for merge $1 of PR $2:
# the newest successful run on a main commit containing it, or else the newest
# such run not cancelled, or nothing; "reverted <commit> <run id>" when that
# successful run's commit comes after a revert of the merge. Once a containing run has succeeded the merge has
# passed, whatever its own run or a later batch did.
main_ci_of() {
  local merge=$1 pr=$2 id st co sha newest= rev
  while read -r id st co sha; do
    [ "$sha" = "$merge" ] || contains "$merge" "$sha" || continue
    if [ "$co" != cancelled ]; then
      if [ "$st $co" = "completed success" ]; then
        rev=$(reverter_of "$merge" "$sha" "$pr")
        [ -n "$rev" ] && echo "reverted $rev $id" || echo "$st $co $id"
        return
      fi
      [ -n "$newest" ] || newest="$st $co $id"
    fi
    # Runs come newest first; those older than the merge's own cannot contain it.
    [ "$sha" = "$merge" ] && break
  done < <(gh run list -R "$REPO" --workflow required-ci.yml --event push --branch main --limit 30 \
             --json databaseId,status,conclusion,headSha \
             -q '.[]|"\(.databaseId) \(.status) \(.conclusion // "" | if . == "" then "none" else . end) \(.headSha)"')
  [ -z "$newest" ] || echo "$newest"
}

# "<status> <conclusion> <run id>" of the dev deploy of $1: the newest successful
# run containing it, or else the newest run containing it, or nothing. Once a
# run containing the merge has succeeded the merge is out, whatever newer runs do.
deploy_of() {
  local merge=$1 id st co sha first=
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
    [ "$st $co" = "completed success" ] && { echo "$st $co $id"; return; }
    [ -n "$first" ] || first="$st $co $id"
  done < <(gh run list -R "$REPO" --workflow deploy-dev.yml --limit 8 \
             --json databaseId,status,conclusion,displayTitle \
             -q '.[]|"\(.databaseId) \(.status) \(.conclusion // "" | if . == "" then "none" else . end) \([.displayTitle|scan("[0-9a-f]{40}")][0] // "-")"')
  [ -z "$first" ] || echo "$first"
}

while :; do
  open=0
  for p in "${prs[@]}"; do
    [ -n "${ended[$p]:-}" ] && continue
    open=1
    j=$(pr_json "$p") || continue
    fields=$(printf '%s' "$j" | pr_fields) || continue
    IFS='|' read -r state head merge mergeable mstate auto <<<"$fields"

    if [ "$state" = MERGED ]; then
      c=$(main_ci_of "$merge" "$p")
      if [ "${main_last[$p]:-unseen}" != "$c" ]; then
        case "$c" in
          "") say "$p" "merged as ${merge:0:9}; waiting for main Required CI" ;;
          "reverted "*) ;;
          *) say "$p" "main Required CI run ${c##* }: ${c% *}" ;;
        esac
        # Any change of state (a newer run, a re-run of the red one) restarts
        # the red wait when main is red again.
        main_last[$p]=$c red_run[$p]=
      fi
      case "$c" in
        "completed success "*) ;;
        "reverted "*)
          rev=${c#reverted }; rev=${rev% *}
          say "$p" "stopped watching: merge ${merge:0:9} was reverted by ${rev:0:9} before main Required CI passed with it"
          ended[$p]=1; continue ;;
        "completed failure "*|"completed timed_out "*|"completed action_required "*|"completed startup_failure "*|"completed stale "*)
          # The newest containing run is red. A newer batch may still fix main,
          # so wait for one.
          if [ "${red_run[$p]:-}" != "${c##* }" ]; then
            red_run[$p]=${c##* } red_since[$p]=$(date +%s)
            say "$p" "main is red after merge ${merge:0:9} (run ${c##* }); waiting up to $((MAIN_RED_WAIT / 60)) min for a newer main batch containing it"
          fi
          if [ $(( $(date +%s) - ${red_since[$p]} )) -ge "$MAIN_RED_WAIT" ]; then
            say "$p" "stopped watching: main Required CI failed after merge ${merge:0:9} (run ${c##* }; no newer batch in $((MAIN_RED_WAIT / 60)) min)"
            ended[$p]=1
          else
            quiet "$p" "merged; main is red (run ${c##* }), waiting for a newer batch"
          fi
          continue ;;
        *) quiet "$p" "merged; waiting for main Required CI"; continue ;;
      esac
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
          if [ -n "$live" ] && contains "$merge" "$live"; then
            rev=$(reverter_of "$merge" "$live" "$p")
            if [ -n "$rev" ]; then
              say "$p" "stopped watching: dev runs ${live:0:9}, after ${rev:0:9} reverted merge ${merge:0:9}"
              ended[$p]=1
            else
              say "$p" "live on dev at ${live:0:9}"; ended[$p]=1 ok[$p]=1
            fi
          elif [ "${last[$p]:-}" != "$key live" ]; then
            say "$p" "deploy run ${d##* } succeeded but dev runs ${live:0:9}, which lacks it; waiting"
            last[$p]="$key live"
          else
            quiet "$p" "merged; dev runs ${live:0:9}, which lacks it"
          fi ;;
        "completed cancelled "*) quiet "$p" "merged; waiting for a superseding dev deploy" ;;
        completed\ *) ended[$p]=1 ;;
        "") quiet "$p" "merged; no dev deploy contains it yet" ;;
        *) quiet "$p" "merged; dev deploy run ${d##* }: ${d% *}" ;;
      esac
      continue
    fi
    if [ "$state" = CLOSED ]; then say "$p" "closed without merging"; ended[$p]=1; continue; fi

    if [ "$mergeable" = CONFLICTING ] || [ "$mstate" = DIRTY ]; then
      say "$p" "stopped watching: conflicts with its base branch"
      ended[$p]=1; continue
    fi

    ci=$(ci_required "$p")
    rv=""
    if [ "$enqueue" = 1 ] && [ "$ci" = pass ] && [ -z "$auto" ]; then
      if rv=$(review_ready "$p") && [[ "$rv" == "可递：head ${head:0:8}｜"* ]]; then
        rv="可递"
      else
        rv="not ready: ${rv//$'\n'/; }"
      fi
    fi
    key="$head|$ci|$auto|$rv"
    if [ "${last[$p]:-}" = "$key" ]; then
      quiet "$p" "CI required: $ci; ${rv:-waiting for merge}${auto:+, $auto}; merge state $mstate"
      # A previous merge request may have failed transiently; re-check the head
      # and retry only after ready was checked again in this round.
      [ "$rv" = 可递 ] || continue
    else
      last[$p]=$key
      say "$p" "CI required: $ci${auto:+; $auto}"
      if [ -n "$rv" ] && [ "$rv" != 可递 ]; then
        say "$p" "not merged, review is not done: ${rv#not ready: }"
      fi
    fi
    if [ "$ci" = fail ]; then say "$p" "stopped watching: CI required failed"; ended[$p]=1; continue; fi
    if [ "$enqueue" = 1 ] && [ -n "$auto" ]; then
      say "$p" "stopped watching: existing auto-merge is not guarded by ready; disable it before retrying"
      ended[$p]=1
      continue
    fi
    if [ "$rv" = 可递 ]; then
      fresh=$(pr_json "$p") || continue
      fields=$(printf '%s' "$fresh" | pr_fields) || continue
      IFS='|' read -r fresh_state fresh_head _ fresh_mergeable fresh_mstate fresh_auto <<<"$fields"
      if [ "$fresh_state" != OPEN ] || [ "$fresh_head" != "$head" ] || [ -n "$fresh_auto" ] \
         || [ "$fresh_mergeable" != MERGEABLE ] || [ "$fresh_mstate" = DIRTY ]; then
        say "$p" "not merged: head or mergeability changed after ready; checking again"
        continue
      fi
      if outcome=$(gh pr merge "$p" -R "$REPO" --squash --match-head-commit "$head" 2>&1); then
        say "$p" "squash merge requested for ${head:0:9}"
      else
        say "$p" "merge refused: ${outcome//$'\n'/; }"
      fi
    fi
  done
  [ "$open" = 1 ] || break
  sleep "$INTERVAL"
done

status=0
for p in "${prs[@]}"; do [ -n "${ok[$p]:-}" ] || status=1; done
echo "$(date -u +%H:%M:%SZ) done: ${#prs[@]} PR(s), exit $status"
exit $status
