#!/usr/bin/env bash
# One-time cutover of the dev/test box (192.168.16.5) from the bare-metal
# source-build service to the unified Docker stack. dev is disposable and has
# bare-metal as an instant fallback, so this goes for the clean end-state: NO
# host nginx (the frontend image already bundles nginx that serves the SPA and
# reverse-proxies /api to the backend), containers take :80 and :8081 directly.
#
# Reversible: on any health failure it restores the bare-metal service + nginx
# exactly as they were. Run ONCE on the dev box:  bash dev-docker-cutover.sh <sha>
set -uo pipefail

SHA="${1:?usage: dev-docker-cutover.sh <image-sha>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="$HERE/compose/docker-compose.base.yml"
PROJECT="cheese"
SVC="cheese-backend-py.service"

log()  { echo "[cutover $(date '+%H:%M:%S')] $*"; }
fail() { echo "[cutover $(date '+%H:%M:%S')] ERROR: $*" >&2; }

dc() { IMAGE_TAG="$SHA" FRONTEND_PORT=80 BACKEND_PORT=8081 \
       docker compose -f "$COMPOSE" -p "$PROJECT" "$@"; }

rollback() {
  fail "rolling back to bare-metal"
  dc down 2>/dev/null || true
  sudo systemctl start "$SVC" 2>/dev/null || true
  sudo systemctl start nginx 2>/dev/null || true
  sleep 3
  local code; code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:80/ || true)"
  fail "bare-metal restored (:80 -> $code). Cutover aborted."
  exit 1
}

[ -f "$COMPOSE" ] || { fail "compose not found: $COMPOSE"; exit 1; }
command -v docker >/dev/null || { fail "docker missing"; exit 1; }

# --- 1. pull + migrate BEFORE stopping bare-metal (minimize downtime window) ---
log "pulling images for $SHA…"
dc pull backend frontend || { fail "pull failed (bare-metal untouched)"; exit 1; }
log "migrating (alembic upgrade head)…"
dc run --rm backend sh -c "alembic upgrade head" || { fail "migrate failed (bare-metal untouched)"; exit 1; }

# --- 2. free the ports: stop bare-metal uvicorn + host nginx ---
log "stopping bare-metal ($SVC + nginx) to free :80/:8081…"
sudo systemctl stop "$SVC" || { fail "could not stop $SVC"; exit 1; }
sudo systemctl stop nginx  || { rollback; }

# --- 3. bring the Docker stack up on :80 + :8081 ---
log "starting Docker stack on :80 (frontend) + :8081 (backend)…"
dc up -d backend frontend || rollback

# --- 4. health-gate; roll back on any failure ---
log "health check…"
ok=0
for _ in $(seq 1 15); do
  sleep 3
  bc="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/healthz || true)"
  fc="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:80/ || true)"
  ac="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:80/api/users/me || true)"
  if [ "$bc" = 200 ] && [ "$fc" = 200 ]; then ok=1; break; fi
done
[ "$ok" = 1 ] || rollback

# --- 5. disable bare-metal auto-start so a reboot doesn't reclaim the ports ---
log "disabling bare-metal service auto-start (kept installed for rollback)…"
sudo systemctl disable "$SVC" 2>/dev/null || true
sudo systemctl disable nginx 2>/dev/null || true

log "CUTOVER OK: dev now served by Docker (frontend :80=$fc, backend :8081=$bc, /api=$ac)"
log "  rollback if ever needed: docker compose -f $COMPOSE -p $PROJECT down; sudo systemctl enable --now $SVC nginx"
