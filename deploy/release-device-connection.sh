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

curl -fsS -m 3 "http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}/release-ready" >/dev/null || {
  echo "device connection owner has active executor calls; release stopped" >&2
  exit 1
}

if [ "${DEPLOY_APP_IMAGE_SOURCE:-registry}" = registry ]; then
  docker compose "${compose_args[@]}" -p "$PROJECT" pull device-connection
else
  docker image inspect "$DEVICE_CONNECTION_IMAGE" >/dev/null
fi
docker compose "${compose_args[@]}" -p "$PROJECT" up -d --no-deps --force-recreate device-connection

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}/healthz" >/dev/null; then
    docker compose "${compose_args[@]}" -p "$PROJECT" ps device-connection
    exit 0
  fi
  sleep 2
done
echo "device connection owner did not become healthy" >&2
exit 1
