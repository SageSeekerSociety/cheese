#!/usr/bin/env bash
# Both deployment entry points check the exact main SHA's build and Required CI first.
set -euo pipefail
umask 077

[[ "${GITHUB_ACTIONS:-}" = true && "${METERING_ALLOW_INTERRUPT:-}" = 1 ]] || {
  echo 'Use the Release metering proxy workflow and acknowledge stream interruption.' >&2
  exit 1
}
sha="${1:?usage: release-metering-proxy.sh <full-main-sha>}"
[[ "$sha" =~ ^[0-9a-f]{40}$ ]] || exit 1
here="$(cd "$(dirname "$0")" && pwd)"
proxy_home="${METERING_PROXY_HOME:-$HOME/cheese-proxy-new/deploy/metering-proxy}"
env_file="$proxy_home/.env"
[[ -r "$env_file" ]] || { echo 'Metering proxy environment file is missing.' >&2; exit 1; }
image="ghcr.io/sageseekersociety/cheese/metering-proxy:$sha"
# A registry hiccup (a dropped connection mid-manifest, `EOF`) fails one pull
# and nothing else, so the pull is tried again before the release gives up;
# deploy-docker.sh retries the app images' pulls the same way.
pulled=false
for delay in ${METERING_PULL_RETRY_DELAYS:-10 30 60} ""; do
  if docker pull "$image"; then pulled=true; break; fi
  [[ -n "$delay" ]] || break
  echo "Pulling $image failed; trying again in ${delay}s." >&2
  sleep "$delay"
done
$pulled || { echo "Pulling $image failed on every attempt." >&2; exit 1; }
export METERING_PROXY_IMAGE
METERING_PROXY_IMAGE="$(docker image inspect "$image" --format '{{index .RepoDigests 0}}')"
[[ "$METERING_PROXY_IMAGE" =~ ^ghcr.io/sageseekersociety/cheese/metering-proxy@sha256:[0-9a-f]{64}$ ]] || {
  echo 'Pulled image has no expected immutable registry digest.' >&2
  exit 1
}
project="$(docker inspect cheese-metering-proxy --format '{{index .Config.Labels "com.docker.compose.project"}}')"
working_dir="$(docker inspect cheese-metering-proxy --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}')"
[[ "$project" = metering-proxy && "$working_dir" = "$proxy_home" ]] || {
  echo 'Existing metering proxy project or working directory differs from the configured target.' >&2
  exit 1
}
# The operator's login tool runs on this box, next to the credential it
# writes. Installed before the unchanged-image exit below, so a release that
# changes only the tool still delivers it.
install -m 0755 "$here/metering-proxy/claude-login.sh" "$proxy_home/claude-login.sh"
install -m 0755 "$here/metering-proxy/chatgpt-login.sh" "$proxy_home/chatgpt-login.sh"
current_image="$(docker inspect cheese-metering-proxy --format '{{.Config.Image}}')"
current_health="$(docker inspect cheese-metering-proxy --format '{{.State.Running}} {{if .State.Health}}{{.State.Health.Status}}{{end}}')"
# The configuration a container runs is the compose file and the box's .env it
# was created from, and a change to either alone (a new env value, a new mount)
# ships no new image. So the release labels the container with a hash of the
# two, and the unchanged-image exit below also needs the hash unchanged. Only
# the hash is kept anywhere: the .env holds secrets.
export METERING_PROXY_CONFIG_SHA256
METERING_PROXY_CONFIG_SHA256="$(
  { sha256sum < "$here/metering-proxy/compose.yml"; sha256sum < "$env_file"; } \
    | sha256sum | cut -d' ' -f1
)"
current_config="$(docker inspect cheese-metering-proxy --format '{{index .Config.Labels "cheese.metering-proxy.config-sha256"}}')"
# Promoted tags can resolve to the running digest; keep its active streams intact.
if [[ "$current_image" = "$METERING_PROXY_IMAGE" && "$current_health" = "true healthy" \
  && "$current_config" = "$METERING_PROXY_CONFIG_SHA256" ]]; then
  echo "Metering proxy already healthy at $METERING_PROXY_IMAGE with this configuration; no restart needed."
  exit 0
fi
release_dir="$proxy_home/releases/${sha}-${GITHUB_RUN_ID:?}-${GITHUB_RUN_ATTEMPT:?}"
mkdir -p "$release_dir"
# Created here, as the operator, so the login script can write the credential
# into it; left to docker, the bind source would be created owned by root.
mkdir -p "$proxy_home/claude-credential" "$proxy_home/chatgpt-credential"
# The private network the gateway reaches the ChatGPT listener on. Shared with
# the gateway stack, whose release may not have run yet; created here if
# missing, and a concurrent creation by that release is not an error.
docker network inspect cheese-meter-gateway >/dev/null 2>&1 \
  || docker network create --internal cheese-meter-gateway >/dev/null \
  || docker network inspect cheese-meter-gateway >/dev/null
previous_image="$(docker inspect cheese-metering-proxy --format '{{.Image}}')"
previous_compose="$(docker inspect cheese-metering-proxy --format '{{index .Config.Labels "com.docker.compose.project.config_files"}}')"
# Keep raw configuration, never docker compose config: its output contains secrets.
# A prior rollback uses several compose files; preserve that ordered list too.
IFS=, read -r -a previous_files <<< "$previous_compose"
previous_args=()
index=0
for file in "${previous_files[@]}"; do
  [[ -f "$file" ]] || { echo 'Existing metering compose file is missing.' >&2; exit 1; }
  snapshot="$release_dir/previous-$index.yml"
  cp "$file" "$snapshot"
  previous_args+=(-f "$snapshot")
  index=$((index + 1))
done
[[ "$index" -gt 0 ]] || exit 1
printf '%s\n' "$previous_image" > "$release_dir/previous-image"
printf '%s\n' "$METERING_PROXY_IMAGE" > "$release_dir/image"
cp "$here/metering-proxy/compose.yml" "$release_dir/compose.yml"
# The legacy addon source, environment, ledger and CA remain in their original
# locations. The saved compose still references them if the first rollout fails.
compose=(docker compose --project-directory "$proxy_home" --env-file "$env_file" -p metering-proxy)
echo "Releasing metering proxy $sha as $METERING_PROXY_IMAGE; active streams may be interrupted."
if "${compose[@]}" -f "$release_dir/compose.yml" up -d --force-recreate --no-deps --wait --wait-timeout 150 metering-proxy; then
  echo "Metering proxy healthy: $METERING_PROXY_IMAGE"
else
  echo 'Metering proxy failed health verification; restoring the previous image and configuration.' >&2
  export METERING_PROXY_IMAGE="$previous_image"
  # The first release's compose names the upstream image literally. The label
  # is emptied because the restored container runs the previous configuration,
  # not the one hashed above, so the next release must not take it as current.
  printf 'services:\n  metering-proxy:\n    image: "%s"\n    labels:\n      cheese.metering-proxy.config-sha256: ""\n' \
    "$previous_image" > "$release_dir/rollback.yml"
  "${compose[@]}" "${previous_args[@]}" -f "$release_dir/rollback.yml" \
    up -d --no-deps --wait --wait-timeout 150 metering-proxy
  # Legacy containers have no Docker HEALTHCHECK; probe the real listeners too.
  for attempt in {1..30}; do
    if docker exec -i cheese-metering-proxy python - < "$here/metering-proxy/healthcheck.py"; then
      echo 'Previous metering proxy restored and healthy.' >&2
      exit 1
    fi
    sleep 2
  done
  echo 'Rollback listeners failed health verification.' >&2
  exit 1
fi
