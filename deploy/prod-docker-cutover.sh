#!/usr/bin/env bash
# One-time cutover of the prod (RUC) box (192.168.16.8, cheese.ruc.edu.cn) from
# the bare-metal source-build service to the unified Docker stack. Same shape as
# the (validated) dev cutover, but PROD — so it takes a fresh verified backup
# first, and every failure restores bare-metal exactly.
#
# The ghg edge (rucfd -> APISIX, 192.168.16.13) terminates public TLS and proxies
# to this box on :8080 (frontend) + :8081 (backend) — NOT the box's :80/:443
# nginx, which is therefore dropped (proven by ss/tcpdump on this box). Uploads
# (the 赛题 PDFs) live at /home/nictheboy/shared/uploads, mounted by the compose,
# untouched by the swap.
#
# Run ONCE on the prod box:  bash prod-docker-cutover.sh <image-sha>
set -uo pipefail

# Box-local deploy overrides (chmod-600, NOT in git — same pattern as ~/ops/r2.env):
# e.g. prod (RUC) pins its release images (BACKEND_IMAGE/FRONTEND_IMAGE, old
# pre-rename path for v0.16.4) and its WS-edge override (VITE_CONNECTOR_WS_BASE).
if [ -f "$HOME/ops/deploy.env" ]; then
  set -a; . "$HOME/ops/deploy.env"; set +a
fi

SHA="${1:?usage: prod-docker-cutover.sh <image-sha>}"
HERE="$(cd "$(dirname "$0")" && pwd)"
COMPOSE="$HERE/compose/docker-compose.base.yml"
PROJECT="cheese"
SVC="cheese-backend-py.service"

log()  { echo "[cutover $(date '+%H:%M:%S')] $*"; }
fail() { echo "[cutover $(date '+%H:%M:%S')] ERROR: $*" >&2; }

# Compose defaults to the edge-facing binding (0.0.0.0 :8080+:80 frontend,
# 0.0.0.0:8081 backend) — don't override the ports.
dc() { IMAGE_TAG="$SHA" docker compose -f "$COMPOSE" -p "$PROJECT" "$@"; }

rollback() {
  fail "rolling back to bare-metal"
  dc down 2>/dev/null || true
  sudo systemctl start "$SVC" 2>/dev/null || true
  sudo systemctl start nginx 2>/dev/null || true
  sleep 3
  local code; code="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ || true)"
  fail "bare-metal restored (:8080 -> $code). Cutover aborted."
  exit 1
}

[ -f "$COMPOSE" ] || { fail "compose not found: $COMPOSE"; exit 1; }
command -v docker >/dev/null || { fail "docker missing"; exit 1; }

# --- 0. fresh VERIFIED backup before touching production (DB + 赛题 uploads) ---
log "Phase 0: fresh backup (DB + uploads → R2)"
[ -x "$HOME/ops/db-backup.sh" ] && { "$HOME/ops/db-backup.sh" || { fail "DB backup failed — abort"; exit 1; }; }
TS="$(date '+%Y%m%d-%H%M%S')"; UPTAR="$HOME/backups/uploads-precutover-$TS.tar.gz"
tar -C "$HOME/shared/uploads" -czf "$UPTAR" . || { fail "uploads tar failed — abort"; exit 1; }
UC="$(tar -tzf "$UPTAR" | grep -cv '/$' || true)"
log "uploads backup: $(du -h "$UPTAR" | cut -f1), $UC files"
if [ -f "$HOME/ops/r2.env" ] && [ -f "$HOME/ops/r2-upload.py" ]; then
  ( set -a; . "$HOME/ops/r2.env"; set +a
    R2_PREFIX=prod-uploads-snapshots "$HOME/cheese-backend-py/backend/.venv/bin/python" "$HOME/ops/r2-upload.py" "$UPTAR" ) \
    || log "WARN: R2 uploads snapshot push failed (local tar + hourly mirror still cover it)"
fi

# --- 1. pull + migrate BEFORE stopping bare-metal (minimize downtime) ---
log "pulling images for $SHA…"
dc pull backend frontend || { fail "pull failed (bare-metal untouched)"; exit 1; }
log "migrating (alembic upgrade head)…"
dc run --rm backend sh -c "alembic upgrade head" || { fail "migrate failed (bare-metal untouched)"; exit 1; }

# --- 2. free the ports: stop bare-metal uvicorn + host nginx ---
log "stopping bare-metal ($SVC + nginx) to free :8080/:8081…"
sudo systemctl stop "$SVC" || { fail "could not stop $SVC"; exit 1; }
sudo systemctl stop nginx  || { rollback; }

# --- 3. bring the Docker stack up (0.0.0.0 :8080+:80 frontend, :8081 backend) ---
log "starting Docker stack…"
dc up -d backend frontend || rollback

# --- 4. health-gate + 赛题 probe; roll back on any failure ---
log "health check…"
ok=0
for _ in $(seq 1 15); do
  sleep 3
  bc="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/healthz || true)"
  fc="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ || true)"
  ac="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/uploads/avatars/432 || true)"
  if [ "$bc" = 200 ] && [ "$fc" = 200 ]; then ok=1; break; fi
done
[ "$ok" = 1 ] || rollback
[ "$ac" = 200 ] || { fail "赛题 uploads probe -> $ac (expected 200)"; rollback; }

# --- 5. disable bare-metal auto-start so a reboot doesn't reclaim the ports ---
log "disabling bare-metal service auto-start (kept installed for rollback)…"
sudo systemctl disable "$SVC" 2>/dev/null || true
sudo systemctl disable nginx 2>/dev/null || true

log "CUTOVER OK: prod now served by Docker (frontend :8080=$fc, backend :8081=$bc, 赛题=$ac)"
log "  rollback if ever needed: docker compose -f $COMPOSE -p $PROJECT down; sudo systemctl enable --now $SVC nginx"
