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

# Optional overlay compose files layered on top of the base (space-separated).
# Bare names resolve against the committed compose dir; absolute paths pass
# through. Set in the box-local ops/deploy.env — e.g. dev adds the subscription
# overlay (docker.sock + workspace path parity, so a turn can spawn the agent
# sandbox); prod leaves it empty and is untouched. Committed overlays survive the
# runner's per-run re-checkout, so the deploy carries them itself — no box-side
# heal hack needed to re-apply them after each CI redeploy.
COMPOSE_OVERLAYS="${COMPOSE_OVERLAYS:-}"
_overlay_args=()  # populated after fail() exists so a missing overlay aborts loudly

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

echo "$(date -Iseconds) $SHA" >> "$HERE/deploy-docker.log"
log "DEPLOY OK: sha=$SHA healthy"

# Reclaim disk from superseded per-commit images: every deploy pulls a fresh
# 6-7GB image set and nothing ever pruned them — the dev box filled its disk to
# 100% (2026-07-18) and CD wedged for a day. Keep anything younger than 72h
# (covers the rollback-to-previous-sha path); best-effort, never fails a deploy.
# NOT time-filtered: under a busy merge day every image is "too new" to prune
# and the disk fills anyway (happened twice on 2026-07-18/19 — 8 image sets in
# an afternoon). Keep only what running containers use; rollback re-pulls from
# ghcr (slower but always available).
log "pruning all unused docker images…"
docker image prune -af >/dev/null 2>&1 || true
docker builder prune -af >/dev/null 2>&1 || true
