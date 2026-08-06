#!/usr/bin/env bash
set -euo pipefail

ROOT_PATH="${CHEESEX_DISK_GUARD_ROOT_PATH:-/}"
THRESHOLD_PERCENT="${CHEESEX_DISK_GUARD_THRESHOLD_PERCENT:-85}"
HEALTH_URL="${CHEESEX_DISK_GUARD_HEALTH_URL:-http://127.0.0.1:8081/health}"
BACKEND_CONTAINER="${CHEESEX_DISK_GUARD_BACKEND_CONTAINER:-cheese-backend-1}"
SETTLE_SECONDS="${CHEESEX_DISK_GUARD_SETTLE_SECONDS:-2}"

log() {
  logger -t cheesex-disk-pressure-guard -- "$*" || true
  printf '%s\n' "$*"
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
