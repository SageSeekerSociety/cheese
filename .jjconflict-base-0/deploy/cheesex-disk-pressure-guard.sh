#!/usr/bin/env bash
set -euo pipefail

ROOT_PATH="${CHEESEX_DISK_GUARD_ROOT_PATH:-/}"
THRESHOLD_PERCENT="${CHEESEX_DISK_GUARD_THRESHOLD_PERCENT:-85}"
HEALTH_URL="${CHEESEX_DISK_GUARD_HEALTH_URL:-http://127.0.0.1:8081/health}"
BACKEND_CONTAINER="${CHEESEX_DISK_GUARD_BACKEND_CONTAINER:-cheese-backend-1}"
SETTLE_SECONDS="${CHEESEX_DISK_GUARD_SETTLE_SECONDS:-2}"

# Early-warning tiers, strictly below THRESHOLD_PERCENT: visibility only, never
# touch cleanup. WARN/HIGH give a heads-up long before the 85% cleanup trigger.
WARN_PERCENT="${CHEESEX_DISK_GUARD_WARN_PERCENT:-70}"
HIGH_PERCENT="${CHEESEX_DISK_GUARD_HIGH_PERCENT:-80}"
TIER_STATE_FILE="${CHEESEX_DISK_GUARD_TIER_STATE_FILE:-/run/cheesex-disk-pressure-guard.tier}"

log() {
  local priority="${2:-notice}"
  logger -t cheesex-disk-pressure-guard -p "daemon.${priority}" -- "$1" || true
  printf '%s\n' "$1"
}

# Log once per tier transition (not every 5-minute run) so a long stretch spent
# in one tier does not spam syslog. State lives in a file because this is a
# oneshot script with no long-lived process to hold the previous tier in memory
# — same idiom as cheesex-healthcheck.sh's /run/*.failures counter.
tier_for_usage() {
  local pct="$1"
  if (( pct >= THRESHOLD_PERCENT )); then
    echo critical
  elif (( pct >= HIGH_PERCENT )); then
    echo high
  elif (( pct >= WARN_PERCENT )); then
    echo warn
  else
    echo ok
  fi
}

report_disk_tier() {
  local usage="$1"
  case "$WARN_PERCENT" in
    ''|*[!0-9]*) log "invalid warn tier threshold: $WARN_PERCENT; skipping tiered disk alerts" warning; return 0 ;;
  esac
  case "$HIGH_PERCENT" in
    ''|*[!0-9]*) log "invalid high tier threshold: $HIGH_PERCENT; skipping tiered disk alerts" warning; return 0 ;;
  esac

  local current_tier previous_tier
  current_tier="$(tier_for_usage "$usage")"
  previous_tier="ok"
  if test -r "$TIER_STATE_FILE"; then
    read -r previous_tier < "$TIER_STATE_FILE" || previous_tier="ok"
  fi
  case "$previous_tier" in
    ok|warn|high|critical) ;;
    *) previous_tier="ok" ;;
  esac

  if [[ "$current_tier" != "$previous_tier" ]]; then
    case "$current_tier" in
      warn)
        log "root usage ${usage}% is in WARN tier (>= ${WARN_PERCENT}%; cleanup triggers at ${THRESHOLD_PERCENT}%)" warning
        ;;
      high)
        log "root usage ${usage}% is in HIGH tier (>= ${HIGH_PERCENT}%; cleanup triggers at ${THRESHOLD_PERCENT}%)" err
        ;;
      critical)
        : # the cleanup path below already logs usage with full context
        ;;
      ok)
        log "root usage ${usage}% is back to OK tier (< ${WARN_PERCENT}%)" notice
        ;;
    esac
  fi
  printf '%s\n' "$current_tier" > "$TIER_STATE_FILE" 2>/dev/null || true
}

root_usage() {
  df -P "$ROOT_PATH" | awk 'NR == 2 {gsub(/%/, "", $5); print $5}'
}

active_turns() {
  curl --fail --silent --show-error --max-time 5 "$HEALTH_URL" \
    | python3 -c '
import json
import sys

value = json.load(sys.stdin)["data"]["active_turns"]
if not isinstance(value, int) or isinstance(value, bool) or value < 0:
    raise SystemExit(1)
print(value)
'
}

usage="$(root_usage)"
case "$usage" in
  ''|*[!0-9]*)
    log "invalid root filesystem usage: ${usage:-empty}; refusing cleanup"
    exit 0
    ;;
esac
case "$THRESHOLD_PERCENT" in
  ''|*[!0-9]*)
    log "invalid threshold: $THRESHOLD_PERCENT; refusing cleanup"
    exit 0
    ;;
esac

report_disk_tier "$usage"

if (( usage < THRESHOLD_PERCENT )); then
  exit 0
fi

first_active="$(active_turns 2>/dev/null)" || {
  log "root usage ${usage}% but backend health is unavailable; refusing cleanup"
  exit 0
}
if (( first_active != 0 )); then
  log "root usage ${usage}% but ${first_active} turn(s) are active; deferring cleanup"
  exit 0
fi

sleep "$SETTLE_SECONDS"
second_active="$(active_turns 2>/dev/null)" || {
  log "root usage ${usage}% but the confirmation health check failed; refusing cleanup"
  exit 0
}
if (( second_active != 0 )); then
  log "root usage ${usage}% and a turn became active; deferring cleanup"
  exit 0
fi

sandbox_ids="$(docker ps -aq --filter label=cheesex-sandbox=1)"
if test -n "$sandbox_ids"; then
  sandbox_count="$(printf '%s\n' "$sandbox_ids" | wc -l | tr -d ' ')"
  log "root usage ${usage}%; removing ${sandbox_count} reconstructible sandbox(es)"
  # Docker IDs contain only hexadecimal characters, so deliberate word splitting
  # here cannot introduce an option or shell metacharacter.
  # shellcheck disable=SC2086
  docker rm -f $sandbox_ids
fi

if docker inspect "$BACKEND_CONTAINER" >/dev/null 2>&1; then
  docker exec "$BACKEND_CONTAINER" sh -c \
    "find /tmp -mindepth 1 -maxdepth 1 -type d \\
      \( -name 'tmp.*' -o -name 'pytest-of-*' \) -mmin +360 \\
      -exec rm -rf -- {} +"
fi

after="$(root_usage)"
log "disk-pressure cleanup complete: root ${usage}% -> ${after}%"
