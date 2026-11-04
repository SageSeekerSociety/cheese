# Retrying a registry operation until a time budget is spent. Sourced by
# deploy-docker.sh (the image pulls), deploy/ghcr-login.sh (the login before
# a deploy), deploy/merge-deployment.sh (its scratch images) and
# deploy/git-fetch.sh (the dev deploy's fetches from github.com, which cross the
# same link). The sourcing script defines log, fail, log_disk <when> and
# reclaim_before_pull_retry (what to do before retrying an attempt that failed
# on a full disk) before calling retry_pull. It may also set PULL_REFUSED_PATTERN
# and PULL_REFUSED_BY (below) when its server words a refusal differently.
#
# Env:
#   DEPLOY_PULL_BUDGET_SECONDS       how long retry_pull keeps retrying, counted
#                                    from its first call in this process (default 600)
#   DEPLOY_PULL_BACKOFF_CAP_SECONDS  the longest wait between attempts (default 30)

# The pulls are retried until 600s after the first one started, waiting 5s,
# 10s, 20s and then 30s between attempts. A pull dies here for transient reasons
# far more often than for real ones. The dev box reaches ghcr through a link
# that resets connections in bursts lasting minutes (2026-10-24: six dev deploy
# attempts gave up on the old three attempts 37-57s after their first pull, each
# on `read: connection reset by peer`), and containerd sometimes fails to commit
# a downloaded layer because its ingest file vanished mid-pull (Deploy run #174,
# 2026-08-09) — a fresh pull opens a NEW ingest record, so a retry clears that
# too. The 30s cap means a pull resumes at most half a minute after a burst
# ends. The ghcr login before a deploy meets the same link (dev deploy run
# 40138894358, 2026-10-24, died on its single login attempt with `read:
# connection reset by peer`), so deploy/ghcr-login.sh retries it here too, with
# a budget of its own: it runs as a step before deploy-docker.sh. The pulls'
# budget is shared by every pull of one deploy and sized against the
# tighter of the two jobs that run deploy-docker.sh: prod's 30-minute limit
# (deploy-prod.yml; dev's is 180). No prod release has run this version yet; on
# dev it took 4-7.5 minutes in the 22 successful runs of 2026-10-24
# (UTC, every pull included) after about a minute of earlier steps, and the one
# prod release of its first version took 10 minutes, so 10 minutes of retrying
# plus a last attempt still ends in the deploy's own error and its reclaim,
# not in the runner killing the job. An image that does not exist at all is
# not what this waits out: both workflows refuse a release
# whose image build did not succeed before this script runs
# (deploy/check-auto-deploy.py), so the full budget is spent only on a manual
# release of a commit that has no images, holding the box's runner 10 minutes.
PULL_BUDGET_SECONDS="${DEPLOY_PULL_BUDGET_SECONDS:-600}"
PULL_BACKOFF_CAP_SECONDS="${DEPLOY_PULL_BACKOFF_CAP_SECONDS:-30}"

case "$PULL_BUDGET_SECONDS" in
  ''|*[!0-9]*) fail "DEPLOY_PULL_BUDGET_SECONDS must be a whole number of seconds (got: '$PULL_BUDGET_SECONDS')" ;;
esac
case "$PULL_BACKOFF_CAP_SECONDS" in
  ''|*[!0-9]*) fail "DEPLOY_PULL_BACKOFF_CAP_SECONDS must be a whole number of seconds (got: '$PULL_BACKOFF_CAP_SECONDS')" ;;
esac

# Wait after the Nth failed attempt: 5s doubling each time, capped.
pull_backoff_delay() {
  local attempt="$1" delay=5 i=1
  while [ "$i" -lt "$attempt" ] && [ "$delay" -lt "$PULL_BACKOFF_CAP_SECONDS" ]; do
    delay=$((delay * 2))
    i=$((i + 1))
  done
  [ "$delay" -le "$PULL_BACKOFF_CAP_SECONDS" ] || delay="$PULL_BACKOFF_CAP_SECONDS"
  printf '%s' "$delay"
}

# Set by the first retry_pull in this process: every retry_pull after it
# shares the budget that is left.
PULL_STARTED=""

# retry_pull <description> <command…>
#
# Keeps `set -euo pipefail` semantics intact: failures are captured explicitly
# rather than swallowed, and once the budget is spent this still calls fail(), so
# the deploy exits non-zero exactly as it did before. The budget decides only
# whether another attempt starts; an attempt already running is not cut short.
# The docker output of each attempt streams to the deploy log as it happens
# (the progress lines are what made the #174 stall diagnosable) and is also kept
# for the attempt, to tell a full disk from everything else.
#
# Only a full disk reclaims before the next attempt. `docker image prune -af`
# deletes every image no container uses yet, which includes this release's
# images pulled moments before. Dev deploy run 40108853373 (attempt 5,
# 2026-10-24) pulled backend and frontend, pruned them when the collab pull was
# reset, and then died pulling backend again for `dc run`. After a dropped
# connection that prune only throws away progress; ENOSPC, which docker reports
# as "no space left on device", is the one failure that freeing space cures.
#
# A registry that refuses the credential ("unauthorized", "denied") fails at
# once: the same token is refused again on every later attempt, and waiting out
# the budget would only delay the error by ten minutes. PULL_REFUSED_PATTERN is
# that test (an extended regex, matched case-insensitively against the
# attempt's output) and PULL_REFUSED_BY names who refused, for the error.
PULL_REFUSED_PATTERN="${PULL_REFUSED_PATTERN:-unauthorized|denied}"
PULL_REFUSED_BY="${PULL_REFUSED_BY:-the registry}"
retry_pull() {
  local what="$1"
  shift
  local attempt=1 rc delay elapsed out errors=""
  [ -n "$PULL_STARTED" ] || PULL_STARTED="$SECONDS"
  out="$(mktemp)"
  while :; do
    rc=0
    "$@" 2>&1 | tee "$out" || rc=$?
    elapsed=$((SECONDS - PULL_STARTED))
    if [ "$rc" -eq 0 ]; then
      [ "$attempt" -eq 1 ] \
        || log "$what: succeeded on attempt $attempt, ${elapsed}s into the pulls' ${PULL_BUDGET_SECONDS}s budget"
      rm -f "$out"
      return 0
    fi
    errors="${errors:+$errors; }attempt $attempt exited $rc"
    if grep -qiE "$PULL_REFUSED_PATTERN" "$out"; then
      rm -f "$out"
      fail "$what was refused by $PULL_REFUSED_BY on attempt $attempt (exit $rc); not retrying a refusal, which a later attempt gets again — see the output above"
    fi
    log_disk "$what failed, attempt $attempt"
    delay="$(pull_backoff_delay "$attempt")"
    [ $((elapsed + delay)) -lt "$PULL_BUDGET_SECONDS" ] || break
    if grep -qi 'no space left on device' "$out"; then
      log "$what: attempt $attempt failed (exit $rc) on a full disk, ${elapsed}s into the pulls' ${PULL_BUDGET_SECONDS}s budget; reclaiming disk, retrying in ${delay}s"
      reclaim_before_pull_retry
    else
      log "$what: attempt $attempt failed (exit $rc), ${elapsed}s into the pulls' ${PULL_BUDGET_SECONDS}s budget; retrying in ${delay}s"
    fi
    sleep "$delay"
    attempt=$((attempt + 1))
  done
  rm -f "$out"
  fail "$what failed after $attempt attempts in ${elapsed}s, the pulls' ${PULL_BUDGET_SECONDS}s budget spent ($errors); see each attempt's docker output above for the underlying error"
}
