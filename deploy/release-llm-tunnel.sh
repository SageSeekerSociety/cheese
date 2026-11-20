#!/usr/bin/env bash
# Release path for the standing model tunnel (`cheese-llm-tunnel`, #551). An app
# deploy never touches it, so that swapping the backend cannot cut machine model
# traffic; it changes when this is dispatched, and a release of the image a
# healthy tunnel already runs leaves it alone. It reads credentials with the
# backend's own code, so a change to how they are read or signed reaches the
# tunnel only through this release.
#
# Like the preview owner it has no drain. Recreating it cuts every open tunnel;
# each machine's helper dials again on its own (`machine_tunnel._dial` rides out
# an absent tunnel with backoff). LLM_TUNNEL_INTERRUPT=1 is the operator's
# explicit acknowledgement of that cut.
#
# The box keeps its own copy of the pair's settings in `~/ops/llm-tunnel/.env`
# (BACKEND_ENV_FILE, and LLM_TUNNEL_IMAGE, which this rewrites after a release
# so `up.sh` brings up what was released) and the api-front files beside it.
set -euo pipefail

SHA="${1:?usage: release-llm-tunnel.sh <image-sha>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="${LLM_TUNNEL_DIR:-$HOME/ops/llm-tunnel}"
[ -f "$DIR/.env" ] || { echo "no tunnel settings at $DIR/.env (see deploy/llm-tunnel/up.sh)" >&2; exit 1; }
export LLM_TUNNEL_IMAGE="ghcr.io/sageseekersociety/cheese/backend:$SHA"
compose=(docker compose -p cheese-dataplane --project-directory "$DIR" --env-file "$DIR/.env"
  -f "$HERE/llm-tunnel/compose.yml")
healthz="http://127.0.0.1:8091/healthz"

if [ "${LLM_TUNNEL_INTERRUPT:-0}" != 1 ]; then
  echo "The model tunnel has no drain: a release cuts every open tunnel (each" >&2
  echo "machine's helper dials again on its own)." >&2
  echo "Re-run with LLM_TUNNEL_INTERRUPT=1 to acknowledge that." >&2
  exit 1
fi

"${compose[@]}" pull llm-tunnel

# Same reasoning as the other owners: the image and configuration it already
# runs change nothing and still cut every tunnel. A wedged tunnel is replaced.
running_image="$(docker inspect --format '{{.Image}}' cheese-llm-tunnel 2>/dev/null || true)"
release_image="$(docker image inspect --format '{{.Id}}' "$LLM_TUNNEL_IMAGE")"
recorded="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' cheese-llm-tunnel 2>/dev/null || true)"
wanted="$("${compose[@]}" config --hash llm-tunnel | awk '$1 == "llm-tunnel" { print $2 }')"
if [ "$running_image" = "$release_image" ] && [ -n "$recorded" ] && [ "$recorded" = "$wanted" ] \
  && curl -fsS -m 3 "$healthz" >/dev/null 2>&1; then
  echo "model tunnel already runs $LLM_TUNNEL_IMAGE ($release_image) with this configuration and is healthy; nothing to release"
  exit 0
fi

"${compose[@]}" up -d --no-deps --force-recreate llm-tunnel

for _ in $(seq 1 30); do
  if curl -fsS -m 3 "$healthz" >/dev/null; then
    sed -i "s#^LLM_TUNNEL_IMAGE=.*#LLM_TUNNEL_IMAGE=$LLM_TUNNEL_IMAGE#" "$DIR/.env"
    "${compose[@]}" ps llm-tunnel
    exit 0
  fi
  sleep 2
done
echo "model tunnel did not become healthy" >&2
exit 1
