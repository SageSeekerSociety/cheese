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

dc() { docker compose -f "$COMPOSE" -p "$PROJECT" "$@"; }
log() { echo "[deploy-docker $(date '+%H:%M:%S')] $*"; }
fail() { echo "[deploy-docker $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

[ -f "$COMPOSE" ] || fail "compose file not found: $COMPOSE"

# Capture the currently-running sha so we can roll back to it on failure.
PREV_SHA="$(dc ps -q backend 2>/dev/null | xargs -r docker inspect \
  --format '{{ index .Config.Labels "com.cheese.image_tag" }}' 2>/dev/null || true)"
if [ -z "$PREV_SHA" ]; then
  # Fall back to reading the image tag actually in use.
  PREV_SHA="$(dc images backend 2>/dev/null | awk 'NR==2{print $3}' || true)"
fi
log "deploying sha=$SHA (previous=${PREV_SHA:-none}) via $COMPOSE"

log "pulling images…"
dc pull backend frontend taskiq-worker taskiq-scheduler || fail "image pull failed"

log "running DB migrations (alembic upgrade head)…"
# Production image ships no pyproject, so call alembic directly from the venv.
dc run --rm backend sh -c "alembic upgrade head" || fail "migration failed — aborting before swap"

log "bringing up backend + frontend + Taskiq runtime…"
dc up -d backend frontend taskiq-worker taskiq-scheduler || fail "compose up failed"

log "waiting for health…"
code=""
for _ in $(seq 1 "$HEALTH_ATTEMPTS"); do
  sleep "$HEALTH_INTERVAL_SECONDS"
  worker_id="$(dc ps --status running -q taskiq-worker 2>/dev/null || true)"
  scheduler_id="$(dc ps --status running -q taskiq-scheduler 2>/dev/null || true)"
  if dc exec -T backend curl -sf http://localhost:8081/healthz >/dev/null 2>&1 \
    && [ -n "$worker_id" ] \
    && [ -n "$scheduler_id" ] \
    && dc exec -T taskiq-worker python -m app.core.taskiq_health >/dev/null 2>&1; then
    code=ok
    break
  fi
done

if [ "$code" != ok ]; then
  log "HEALTH CHECK FAILED"
  if [ -n "${PREV_SHA:-}" ] && [ "$PREV_SHA" != "$SHA" ]; then
    log "rolling back to $PREV_SHA…"
    IMAGE_TAG="$PREV_SHA" dc up -d backend frontend taskiq-worker taskiq-scheduler || true
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
