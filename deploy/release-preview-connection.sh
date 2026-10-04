#!/usr/bin/env bash
# Explicit release path for the stable preview owner (machine preview tunnels
# and preview content hosts). Ordinary app deploys never call this script:
# replacing this service drops every live preview tunnel, so it is released on
# its own, exactly like the device connection owner.
#
# Unlike the device connection owner this process has NO drain endpoint. It does
# not hold executor calls that must finish, and a dropped preview tunnel is
# re-dialed by the machine helper on its own (see the redial loop in
# backend/app/domain/agent/preview_tunnel.py), so there is nothing to wait for
# and no graceful middle ground. PREVIEW_CONNECTION_INTERRUPT=1 is therefore
# mandatory here — not a waiver of a wait that does not exist, but the
# operator's explicit acknowledgement that every live preview tunnel will drop
# and redial on this release.
set -euo pipefail

if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi

SHA="${1:?usage: release-preview-connection.sh <image-sha> [compose-file]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${2:-$HERE/compose/docker-compose.base.yml}"
PROJECT="${PROJECT:-cheese}"
export IMAGE_TAG="$SHA"
export PREVIEW_CONNECTION_IMAGE="${PREVIEW_CONNECTION_IMAGE:-${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$SHA}}"
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

if [ "${PREVIEW_CONNECTION_INTERRUPT:-0}" != 1 ]; then
  echo "The preview connection owner has no drain: a release interrupts every" >&2
  echo "live preview tunnel (the machine helpers redial it on their own)." >&2
  echo "Re-run with PREVIEW_CONNECTION_INTERRUPT=1 to acknowledge that." >&2
  exit 1
fi

if [ "${DEPLOY_APP_IMAGE_SOURCE:-registry}" = registry ]; then
  docker compose "${compose_args[@]}" -p "$PROJECT" pull preview-connection
else
  docker image inspect "$PREVIEW_CONNECTION_IMAGE" >/dev/null
fi

docker compose "${compose_args[@]}" -p "$PROJECT" up -d --no-deps --force-recreate preview-connection

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "http://127.0.0.1:${PREVIEW_CONNECTION_PORT:-18087}/healthz" >/dev/null; then
    docker compose "${compose_args[@]}" -p "$PROJECT" ps preview-connection
    exit 0
  fi
  sleep 2
done
echo "preview connection owner did not become healthy" >&2
exit 1
