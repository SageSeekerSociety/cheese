#!/usr/bin/env bash
# Release path for the stable device WebSocket owner. Replacing this service
# disconnects links, so an app deploy calls it only when its migrations drop,
# rename or alter schema the owner's image maps (deploy-docker.sh,
# `hold_owners_behind_schema`); otherwise it is dispatched on its own. A
# dispatched release of the image a healthy owner already runs leaves it alone.
#
# The optional step splits the release around that deploy's migrations:
#   all       drain, then recreate (the default)
#   drain     drain and leave the owner draining, so it takes no new calls
#   recreate  recreate an owner a `drain` left draining
#   resume    let an owner a `drain` left draining take calls again
set -euo pipefail

if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi

SHA="${1:?usage: release-device-connection.sh <image-sha> [compose-file] [all|drain|recreate|resume]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${2:-$HERE/compose/docker-compose.base.yml}"
STEP="${3:-all}"
case "$STEP" in
  all|drain|recreate|resume) ;;
  *) echo "unknown release step: $STEP" >&2; exit 1 ;;
esac
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
# same_config <service> <container> <image variable>: would compose create the
# service from the same configuration it runs, image aside? Compose labels each
# container with the hash of the configuration it was created from; a rotated
# secret, a changed env file or a compose change moves it.
same_config() {
  local reference recorded wanted
  reference="$(docker inspect --format '{{.Config.Image}}' "$2" 2>/dev/null || true)"
  recorded="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.config-hash"}}' "$2" 2>/dev/null || true)"
  wanted="$(env "$3=$reference" docker compose "${compose_args[@]}" -p "$PROJECT" config --hash "$1" 2>/dev/null \
    | awk -v service="$1" '$1 == service { print $2 }')"
  [ -n "$recorded" ] && [ "$recorded" = "$wanted" ]
}

drained=false
resume_owner() {
  [ "$drained" = true ] || return 0
  owner_post /internal/device-connection/release-resume >/dev/null 2>&1 || true
}
trap resume_owner EXIT

if [ "$STEP" = resume ]; then
  owner_post /internal/device-connection/release-resume >/dev/null
  exit
fi

if [ "$STEP" != recreate ]; then
  if [ "${DEPLOY_APP_IMAGE_SOURCE:-registry}" = registry ]; then
    docker compose "${compose_args[@]}" -p "$PROJECT" pull device-connection
  else
    docker image inspect "$DEVICE_CONNECTION_IMAGE" >/dev/null
  fi
  # Replacing an owner with the image and configuration it already runs
  # changes nothing it serves and still closes every link it holds. Only a
  # healthy one is kept: recreating is also how a wedged owner on the same
  # image is restarted, and a draining owner does not answer healthy.
  if [ "$STEP" = all ]; then
    running="$(docker compose "${compose_args[@]}" -p "$PROJECT" ps -q device-connection 2>/dev/null | head -n 1 || true)"
    running_image="$([ -z "$running" ] || docker inspect --format '{{.Image}}' "$running" 2>/dev/null || true)"
    release_image="$(docker image inspect --format '{{.Id}}' "$DEVICE_CONNECTION_IMAGE" 2>/dev/null || true)"
    if [ -n "$running_image" ] && [ "$running_image" = "$release_image" ] \
      && same_config device-connection "$running" DEVICE_CONNECTION_IMAGE \
      && curl -fsS -m 3 "$owner_url/healthz" >/dev/null 2>&1; then
      echo "device connection owner already runs $DEVICE_CONNECTION_IMAGE ($release_image) with this configuration and is healthy; nothing to release"
      exit 0
    fi
  fi
  drain_attempts=180
  drain_interval=0.25
  # The first drain request stops the owner taking new calls; each one it turns
  # away carries the draining header and its caller sends it again once the new
  # container answers (`device_hub_rpc`, `executor_transport`). The drain then
  # waits up to 45 s for the calls already in flight to finish — the reads a
  # runner holds until it has news do not count; one cut short is read again.
  # A call still running after that (a long command) fails the release, which
  # resumes the owner, unless interrupting was asked for.
  #
  # What interrupting costs is the in-flight executor call, and no more. Device
  # links dial out and reconnect on their own, the tmux sessions on the device
  # outlive the connector process, and `adopt_screen` re-binds each screen the cli
  # re-announces — that path exists precisely because this process restarts.
  # `late` waits the whole drain first and interrupts only what is still running.
  interrupt="${DEVICE_CONNECTION_INTERRUPT:-0}"
  for attempt in $(seq 1 "$drain_attempts"); do
    status="$(owner_status /internal/device-connection/release-drain)" || {
      echo "device connection owner drain request failed" >&2
      exit 1
    }
    # The owner stops taking new calls on the first drain request, busy or not;
    # from here on an exit before the recreate has to resume it.
    case "$status" in 200|409) drained=true ;; esac
    case "$status" in
      200) break ;;
      409)
        if [ "$interrupt" = 1 ]; then
          echo "device connection owner is busy; interrupting its in-flight calls as asked" >&2
          break
        fi
        if [ "$attempt" -eq "$drain_attempts" ]; then
          if [ "$interrupt" = late ]; then
            echo "device connection owner is still busy after the drain; interrupting its in-flight calls" >&2
            break
          fi
          echo "device connection owner remained busy; release stopped" >&2
          exit 1
        fi
        sleep "$drain_interval"
        ;;
      *) echo "device connection owner drain returned HTTP $status" >&2; exit 1 ;;
    esac
  done
  if [ "$STEP" = drain ]; then
    # Left draining on purpose: the caller recreates it, or resumes it.
    drained=false
    exit 0
  fi
fi
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
