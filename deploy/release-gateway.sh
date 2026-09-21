#!/usr/bin/env bash
# Called only by the independent gateway release workflow, after its build gate.
set -euo pipefail

[[ "${GITHUB_ACTIONS:-}" = true && "${GATEWAY_ALLOW_INTERRUPT:-}" = 1 ]] || {
  echo 'Use the Release gateway workflow and acknowledge stream interruption.' >&2
  exit 1
}
sha="${1:?usage: release-gateway.sh <full-main-sha>}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 1
here="$(cd "$(dirname "$0")" && pwd)"
gateway_home="${GATEWAY_HOME:-$HOME/gateway}"
env_file="$gateway_home/compose/.env"
[[ -r "$env_file" ]] || { echo 'Gateway credentials file is missing.' >&2; exit 1; }
release_dir="$gateway_home/releases/${sha}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
mkdir -m 700 -p "$release_dir"
export GATEWAY_IMAGE="ghcr.io/sageseekersociety/cheese/gateway:${sha:0:7}"
export GATEWAY_CONFIG="$release_dir/config.yaml"
compose=(docker compose --env-file "$env_file" -f "$here/compose/docker-compose.gateway.yml" -p cheese-gateway)

docker pull "$GATEWAY_IMAGE"
revision="$(docker image inspect "$GATEWAY_IMAGE" --format '{{index .Config.Labels "org.opencontainers.image.revision"}}')"
# Promoted images retain their original revision; the workflow verifies the
# requested commit's build, and its immutable tag selects the resulting image.
[[ -n "$revision" ]] || { echo 'Gateway image has no revision label.' >&2; exit 1; }
previous_image="$(docker inspect cheese-gateway-litellm-1 --format '{{.Image}}')"
docker cp cheese-gateway-litellm-1:/app/config.yaml "$release_dir/previous.yaml"
printf '%s\n' "$previous_image" > "$release_dir/previous-image"
candidate="$(docker create "$GATEWAY_IMAGE")"
trap 'docker rm "$candidate" >/dev/null' EXIT
docker cp "$candidate:/opt/cheese-gateway/config.yaml" "$GATEWAY_CONFIG"
docker rm "$candidate" >/dev/null
trap - EXIT
docker run --rm --network none --entrypoint /app/.venv/bin/python \
  -e LITELLM_LOCAL_MODEL_COST_MAP=True "$GATEWAY_IMAGE" /opt/cheese-gateway/test_deepseek_images.py

echo "Releasing gateway image=$GATEWAY_IMAGE revision=$revision; active streams may be interrupted."
if "${compose[@]}" up -d --no-deps --wait --wait-timeout 150 litellm; then
  echo "Gateway healthy: $GATEWAY_IMAGE"
else
  echo 'Gateway failed health verification; restoring the previous image and configuration.' >&2
  export GATEWAY_IMAGE="$previous_image" GATEWAY_CONFIG="$release_dir/previous.yaml"
  "${compose[@]}" up -d --no-deps --wait --wait-timeout 150 litellm
  exit 1
fi
