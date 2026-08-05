#!/usr/bin/env bash
# Unified Docker deploy for the bare-metal-turned-container boxes (dev / prod RUC).
#
# Pulls the per-commit images, migrates, and brings the app tier up — the Docker
# equivalent of deploy-blue-green.sh, minus the on-box source build (images are
# prebuilt by build.yml). The DB/Redis are EXTERNAL (this script never touches
# them); uploads live on a host path outside the containers. Rollback = redeploy
# the previously-running sha (captured below) and `up -d`.
#
# Usage: deploy-docker.sh <image-sha> [compose-file]
#   image-sha:    the commit sha to deploy (pins backend+frontend together)
#   compose-file: default deploy/compose/docker-compose.base.yml
#
# Env (with safe defaults baked into the compose file):
#   BACKEND_ENV_FILE   path to the box's backend/.env   (default in compose)
#   UPLOADS_HOST_PATH  host dir holding uploads          (default in compose)
#   PROJECT            compose project name              (default cheese)
set -euo pipefail

# Box-local deploy overrides (chmod-600, NOT in git — same pattern as ~/ops/r2.env):
# e.g. prod (RUC) pins its release images (BACKEND_IMAGE/FRONTEND_IMAGE, old
# pre-rename path for v0.16.4) and its WS-edge override (VITE_CONNECTOR_WS_BASE).
if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi

SHA="${1:?usage: deploy-docker.sh <image-sha> [compose-file]}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="${2:-$HERE/compose/docker-compose.base.yml}"
PROJECT="${PROJECT:-cheese}"
HEALTH_ATTEMPTS="${DEPLOY_HEALTH_ATTEMPTS:-15}"
HEALTH_INTERVAL_SECONDS="${DEPLOY_HEALTH_INTERVAL_SECONDS:-3}"
export IMAGE_TAG="$SHA"
export SANDBOX_IMAGE="${SANDBOX_IMAGE:-ghcr.io/sageseekersociety/cheese/sandbox:$SHA}"
export TMUX_SANDBOX_IMAGE="${TMUX_SANDBOX_IMAGE:-ghcr.io/sageseekersociety/cheese/sandbox-tmux:$SHA}"
export QUALITY_GATE_IMAGE="${QUALITY_GATE_IMAGE:-$TMUX_SANDBOX_IMAGE}"

# Optional overlay compose files layered on top of the base (space-separated).
# Bare names resolve against the committed compose dir; absolute paths pass
# through. Set in the box-local ops/deploy.env — e.g. dev adds the subscription
# overlay (docker.sock + workspace path parity, so a turn can spawn the agent
# sandbox); prod leaves it empty and is untouched. Committed overlays survive the
# runner's per-run re-checkout, so the deploy carries them itself — no box-side
# heal hack needed to re-apply them after each CI redeploy.
COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:-}"
_overlay_args=()  # populated after fail() exists so a missing overlay aborts loudly

# Only environments wired for sibling agent containers need the large runtime
# images. Dev's subscription overlay is that signal; production app-only boxes
# stay compatible with historical release SHAs that predate these image tags.
AGENT_RUNTIME_IMAGES_REQUIRED="${AGENT_RUNTIME_IMAGES_REQUIRED:-auto}"
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = auto ]; then
  case "$COMPOSE_OVERLAYS" in
    *docker-compose.subscription.yml*) AGENT_RUNTIME_IMAGES_REQUIRED=true ;;
    *) AGENT_RUNTIME_IMAGES_REQUIRED=false ;;
  esac
fi

dc() {
  docker compose -f "$COMPOSE" ${_overlay_args[@]+"${_overlay_args[@]}"} \
    -p "$PROJECT" "$@"
}
log() { echo "[deploy-docker $(date '+%H:%M:%S')] $*"; }
fail() { echo "[deploy-docker $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

[ -f "$COMPOSE" ] || fail "compose file not found: $COMPOSE"

# Resolve overlays now that fail() is defined; abort if deploy.env names one that
# was not checked out, rather than silently deploying without the wiring.
for _f in $COMPOSE_OVERLAYS; do
  case "$_f" in
    /*) : ;;
    *)  _f="$HERE/compose/$_f" ;;
  esac
  [ -f "$_f" ] || fail "overlay compose not found: $_f"
  _overlay_args+=(-f "$_f")
  log "overlay: $_f"
done

# Capture the currently-running sha so we can roll back to it on failure.
PREV_SHA="$(dc ps -q backend 2>/dev/null | xargs -r docker inspect \
  --format '{{ index .Config.Labels "com.cheese.image_tag" }}' 2>/dev/null || true)"
if [ -z "$PREV_SHA" ]; then
  # Fall back to reading the image tag actually in use.
  PREV_SHA="$(dc images backend 2>/dev/null | awk 'NR==2{print $3}' || true)"
fi
log "deploying sha=$SHA (previous=${PREV_SHA:-none}) via $COMPOSE"

log "pulling images…"
dc pull backend frontend || fail "image pull failed"

# Runtime images are launched on demand through docker.sock, so compose cannot
# pull or retain them for us. Pull both execution paths and run the same minimum
# binary check a real tmux turn needs BEFORE touching the live app.
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  log "pulling agent runtime images…"
  docker pull "$SANDBOX_IMAGE" || fail "SDK sandbox image pull failed: $SANDBOX_IMAGE"
  docker pull "$TMUX_SANDBOX_IMAGE" || fail "tmux sandbox image pull failed: $TMUX_SANDBOX_IMAGE"
  docker run --rm --entrypoint sh "$TMUX_SANDBOX_IMAGE" -c \
    'command -v tmux >/dev/null && command -v ttyd >/dev/null && command -v cheese >/dev/null' \
    || fail "tmux sandbox smoke test failed: $TMUX_SANDBOX_IMAGE"

  # `docker image prune -a` considers an on-demand image unused when no turn is
  # active. Stopped zero-cost containers make the desired runtime images explicit
  # roots, while still allowing superseded versions to be reclaimed each deploy.
  prepare_image_retainer() {
    local kind="$1"
    local image="$2"
    local next="${PROJECT}-${kind}-image-retainer-next"
    docker rm -f "$next" >/dev/null 2>&1 || true
    docker create --name "$next" \
      --label "com.cheese.image-retainer=$kind" \
      --entrypoint /bin/true "$image" >/dev/null \
      || fail "could not retain $kind runtime image: $image"
  }
  prepare_image_retainer sandbox "$SANDBOX_IMAGE"
  prepare_image_retainer tmux "$TMUX_SANDBOX_IMAGE"
fi

log "running DB migrations (alembic upgrade head)…"
# Production image ships no pyproject, so call alembic directly from the venv.
dc run --rm backend sh -c "alembic upgrade head" || fail "migration failed — aborting before swap"

log "bringing up backend + frontend…"
dc up -d backend frontend || fail "compose up failed"

log "waiting for health…"
code=""
app_tier=""
for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  sleep "$HEALTH_INTERVAL_SECONDS"
  app_tier="$(docker ps -a \
    --filter "label=com.docker.compose.project=$PROJECT" \
    --format '{{.Label "com.docker.compose.service"}}\t{{.Image}}\t{{.State}}\t{{.Status}}' \
    2>/dev/null || true)"
  if printf '%s\n' "$app_tier" | "$HERE/check-app-tier.sh" "$SHA" \
    >/dev/null 2>&1; then
    code=ok
    break
  fi
done

if [ "$code" != ok ]; then
  log "HEALTH CHECK FAILED"
  printf '%s\n' "$app_tier" | "$HERE/check-app-tier.sh" "$SHA" || true
  if [ -n "${PREV_SHA:-}" ] && [ "$PREV_SHA" != "$SHA" ]; then
    log "rolling back to $PREV_SHA…"
    IMAGE_TAG="$PREV_SHA" dc up -d backend frontend || true
  fi
  fail "deploy failed health check${PREV_SHA:+, rolled back to $PREV_SHA}"
fi

promote_image_retainer() {
  local kind="$1"
  local current="${PROJECT}-${kind}-image-retainer"
  local next="${current}-next"
  # `next` already protects the new image, so removing the old retainer never
  # leaves either deployment's image unreferenced during the handoff.
  docker rm -f "$current" >/dev/null 2>&1 || true
  docker rename "$next" "$current" \
    || fail "could not promote $kind runtime image retainer"
}
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  promote_image_retainer sandbox
  promote_image_retainer tmux
fi

# Reclaim disk from superseded per-commit images: every deploy pulls a fresh
# 6-7GB image set and nothing ever pruned them — the dev box filled its disk to
# 100% (2026-07-18) and CD wedged for a day. Keep anything younger than 72h
# (covers the rollback-to-previous-sha path); best-effort, never fails a deploy.
# NOT time-filtered: under a busy merge day every image is "too new" to prune
# and the disk fills anyway (happened twice on 2026-07-18/19 — 8 image sets in
# an afternoon). Keep what running containers and the two explicit runtime-image
# retainers use; rollback re-pulls superseded images from ghcr.
log "pruning all unused docker images…"
docker image prune -af >/dev/null 2>&1 || true
# Keep a bounded hot cache on the persistent runner. Wiping it here made every
# subsequent build re-download and rebuild all dependency layers; the build
# workflow independently caps its named BuildKit cache at the same size.
docker builder prune -af --max-used-space 6GB --min-free-space 10GB \
  >/dev/null 2>&1 || true
echo "$(date -Iseconds) $SHA" >> "$HERE/deploy-docker.log"
if [ "$AGENT_RUNTIME_IMAGES_REQUIRED" = true ]; then
  log "DEPLOY OK: sha=$SHA healthy; agent runtime images verified and retained"
else
  log "DEPLOY OK: sha=$SHA healthy"
fi
