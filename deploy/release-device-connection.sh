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
owner_post /internal/device-connection/release-drain >/dev/null || {
  echo "device connection owner has active executor calls; release stopped" >&2
  exit 1
}
drained=true
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
