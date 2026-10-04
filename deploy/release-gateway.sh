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

# Shared with the metering proxy, whose release may not have run yet; created
# here if missing, and a concurrent creation by that release is not an error.
docker network inspect cheese-meter-gateway >/dev/null 2>&1 \
  || docker network create --internal cheese-meter-gateway >/dev/null \
  || docker network inspect cheese-meter-gateway >/dev/null
echo "Releasing gateway image=$GATEWAY_IMAGE revision=$revision; active streams may be interrupted."
# --remove-orphans: a service dropped from the compose file (openai-egress) must not keep running.
if "${compose[@]}" up -d --no-deps --remove-orphans --wait --wait-timeout 150 litellm-redis litellm; then
  echo "Gateway healthy: $GATEWAY_IMAGE"
else
  echo 'Gateway failed health verification; restoring the previous image and configuration.' >&2
  export GATEWAY_IMAGE="$previous_image" GATEWAY_CONFIG="$release_dir/previous.yaml"
  "${compose[@]}" up -d --no-deps --remove-orphans --wait --wait-timeout 150 litellm
  exit 1
fi

# Spend reads page through one key's day by (api_key, startTime) (the backend's
# gateway.py). LiteLLM's schema declares that index but leaves building it to
# operators, because a plain CREATE INDEX blocks spend-log inserts for the whole
# build; without it every page full-scans the spend table. CONCURRENTLY does not
# block inserts. A CONCURRENTLY build that failed leaves an invalid index, which
# IF NOT EXISTS would then skip forever, so that one is dropped first.
"${compose[@]}" exec -T litellm-db psql -U litellm -d litellm -v ON_ERROR_STOP=1 -q <<'SQL'
SELECT 'DROP INDEX CONCURRENTLY "LiteLLM_SpendLogs_api_key_startTime_idx"'
FROM pg_index JOIN pg_class ON pg_class.oid = pg_index.indexrelid
WHERE pg_class.relname = 'LiteLLM_SpendLogs_api_key_startTime_idx' AND NOT pg_index.indisvalid
\gexec
CREATE INDEX CONCURRENTLY IF NOT EXISTS "LiteLLM_SpendLogs_api_key_startTime_idx"
ON "LiteLLM_SpendLogs" ("api_key", "startTime");
SQL
echo "Spend-log index in place."
