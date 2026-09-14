#!/usr/bin/env bash
# Explicit release path for the stable device WebSocket owner. Ordinary app
# deploys never call this script because replacing this service disconnects links.
set -euo pipefail

SHA="${1:?usage: release-device-connection.sh <image-sha> [compose-file]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${2:-$HERE/compose/docker-compose.base.yml}"
PROJECT="${PROJECT:-cheese}"
export IMAGE_TAG="$SHA"
export DEVICE_CONNECTION_IMAGE="${DEVICE_CONNECTION_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}"

docker compose -f "$COMPOSE" -p "$PROJECT" pull device-connection
docker compose -f "$COMPOSE" -p "$PROJECT" up -d --no-deps --force-recreate device-connection

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "http://127.0.0.1:${DEVICE_CONNECTION_PORT:-18083}/healthz" >/dev/null; then
    docker compose -f "$COMPOSE" -p "$PROJECT" ps device-connection
    exit 0
  fi
  sleep 2
done
echo "device connection owner did not become healthy" >&2
exit 1
