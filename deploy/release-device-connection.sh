#!/usr/bin/env bash
# Explicit release path for the stable device WebSocket owner. Ordinary app
# deploys never call this script because replacing this service disconnects links.
set -euo pipefail

if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi

SHA="${1:?usage: release-device-connection.sh <image-sha> [compose-file]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${2:-$HERE/compose/docker-compose.base.yml}"
PROJECT="${PROJECT:-cheese}"
export IMAGE_TAG="$SHA"
export DEVICE_CONNECTION_IMAGE="${DEVICE_CONNECTION_IMAGE:-${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}}"
COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:-}"
compose_args=(-f "$COMPOSE")
for overlay in $COMPOSE_OVERLAYS; do
  case "$overlay" in
    /*) : ;;
    *) overlay="$HERE/compose/$overlay" ;;
  esac
  [ -f "$overlay" ] || { echo "overlay compose not found: $overlay" >&2; exit 1; }
  compose_args+=(-f "$overlay")
done

env_file="${BACKEND_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
[ -r "$env_file" ] || { echo "backend env file not readable: $env_file" >&2; exit 1; }
read_setting() {
  local key="$1" value
  value="$(awk -F= -v key="$key" '$1 == key { sub(/^[^=]*=/, ""); print; exit }' "$env_file")"
  case "$value" in
    \"*\") value="${value#\"}"; value="${value%\"}" ;;
    \'*\') value="${value#\'}"; value="${value%\'}" ;;
  esac
  printf '%s' "$value"
}
owner_secret="${DEVICE_CONNECTION_SECRET:-}"
[ -n "$owner_secret" ] || owner_secret="$(read_setting DEVICE_CONNECTION_SECRET)"
[ -n "$owner_secret" ] || owner_secret="$(read_setting JWT_SECRET)"
[ -n "$owner_secret" ] || { echo "device connection secret is not configured" >&2; exit 1; }
owner_url="http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}"
owner_post() {
  local path="$1"
  printf 'silent\nshow-error\nfail\nmax-time = 3\nrequest = POST\nheader = "X-Device-Connection-Secret: %s"\nurl = "%s%s"\n' \
    "$owner_secret" "$owner_url" "$path" | curl --config -
}
owner_status() {
  local path="$1"
  printf 'silent\nshow-error\nmax-time = 3\nrequest = POST\noutput = /dev/null\nwrite-out = %%{http_code}\nheader = "X-Device-Connection-Secret: %s"\nurl = "%s%s"\n' \
    "$owner_secret" "$owner_url" "$path" | curl --config -
}
drained=false
resume_owner() {
  [ "$drained" = true ] || return 0
  owner_post /internal/device-connection/release-resume >/dev/null 2>&1 || true
}
trap resume_owner EXIT

if [ "${DEPLOY_APP_IMAGE_SOURCE:-registry}" = registry ]; then
  docker compose "${compose_args[@]}" -p "$PROJECT" pull device-connection
else
  docker image inspect "$DEVICE_CONNECTION_IMAGE" >/dev/null
fi
drain_attempts=240
drain_interval=0.25
# An idle owner is what the drain waits for, and on a platform anybody is using
# it does not arrive: `call_executor` is held open under a shield and the
# backend re-polls it about once a second, so `_active_rpc_calls` stays above
# zero for as long as a room has an agent in it — hours, for one turn. A fix
# that lives in this process then cannot ship at all; #1114 sat merged and
# unreleased while the alerts it fixes kept arriving. So the wait can be waived
# deliberately, and only deliberately: the default is unchanged.
#
# What interrupting costs is the in-flight executor call, and no more. Device
# links dial out and reconnect on their own, the tmux sessions on the device
# outlive the connector process, and `adopt_screen` re-binds each screen the cli
# re-announces — that path exists precisely because this process restarts.
interrupt="${DEVICE_CONNECTION_INTERRUPT:-0}"
for attempt in $(seq 1 "$drain_attempts"); do
  status="$(owner_status /internal/device-connection/release-drain)" || {
    echo "device connection owner drain request failed" >&2
    exit 1
  }
  case "$status" in
    200) drained=true; break ;;
    409)
      if [ "$interrupt" = 1 ]; then
        echo "device connection owner is busy; interrupting its in-flight calls as asked" >&2
        break
      fi
      if [ "$attempt" -eq "$drain_attempts" ]; then
        echo "device connection owner remained busy; release stopped" >&2
        exit 1
      fi
      sleep "$drain_interval"
      ;;
    *) echo "device connection owner drain returned HTTP $status" >&2; exit 1 ;;
  esac
done
docker compose "${compose_args[@]}" -p "$PROJECT" up -d --no-deps --force-recreate device-connection

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}/healthz" >/dev/null; then
    docker compose "${compose_args[@]}" -p "$PROJECT" ps device-connection
    drained=false
    exit 0
  fi
  sleep 2
done
echo "device connection owner did not become healthy" >&2
exit 1
