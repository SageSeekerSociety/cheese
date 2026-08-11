#!/usr/bin/env bash
# One-time migration of the prod (RUC) app box (192.168.16.8) onto the blue-green
# release layout that deploy-blue-green.sh expects, so it can join the gated CD.
#
# The box today runs from a plain ~/cheese-backend-py git checkout with
# STORAGE_LOCAL_PATH=./uploads — a relative path, so uploaded files (the 赛题
# PDFs) live INSIDE the checkout. Blue-green replaces the checkout with a fresh
# release dir on every deploy, so relative uploads would vanish on the first one.
# The fix is architectural, not a workaround: application state belongs OUTSIDE
# the deployable release, reached by an absolute path in .env. This script makes
# that true and converts the plain checkout into the release+symlink layout.
#
# Safe to re-run: every phase is guarded and no-ops once applied. It never
# deletes the live data, the service keeps serving through each step, and it
# aborts (leaving a known-good state) the moment a health or data check fails.
#
# Run ONCE on the prod box as user nictheboy:  bash prod-genesis-cutover.sh
set -euo pipefail

BASE="/home/nictheboy"
APP="$BASE/cheese-backend-py"
RELEASES="$BASE/releases"
SHARED_UPLOADS="$BASE/shared/uploads"
BACKUPS="$BASE/backups"
SERVICE="cheese-backend-py.service"
HEALTH_URL="http://127.0.0.1:8081/healthz"
UPLOAD_PROBE="/uploads/avatars/432"   # a known existing file — proves uploads still serve
TS="$(date '+%Y%m%d-%H%M%S')"

log()  { echo "[genesis $(date '+%H:%M:%S')] $*"; }
fail() { echo "[genesis $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

health() {
  local code=""
  for _ in $(seq 1 10); do
    sleep 2
    code="$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)"
    [ "$code" = 200 ] && { log "health OK ($HEALTH_URL -> 200)"; return 0; }
  done
  fail "health check failed (last=$code)"
}
probe_uploads() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8081${UPLOAD_PROBE}" || true)"
  [ "$code" = 200 ] || fail "uploads probe ${UPLOAD_PROBE} -> $code (expected 200) — 赛题 not served!"
  log "uploads probe OK (${UPLOAD_PROBE} -> 200)"
}

command -v pg_dump >/dev/null || fail "pg_dump not found"
[ -e "$APP" ] || fail "$APP missing"
mkdir -p "$RELEASES" "$BACKUPS" "$(dirname "$SHARED_UPLOADS")"

# Resolve where uploads physically are right now (works before and after Phase 1,
# and through the symlink after Phase 2 — $APP always resolves to the live tree).
SRC_UPLOADS="$APP/backend/uploads"
[ -d "$SRC_UPLOADS" ] || fail "$SRC_UPLOADS not found — nothing to migrate"
SRC_COUNT="$(find "$SRC_UPLOADS" -type f | wc -l | tr -d ' ')"
log "live uploads: $SRC_UPLOADS ($(du -sh "$SRC_UPLOADS" | cut -f1), $SRC_COUNT files)"

# ─── Phase 0: fresh belt-and-suspenders backup before touching anything ───
log "Phase 0: fresh DB + uploads backup"
if [ -x "$BASE/ops/db-backup.sh" ]; then
  "$BASE/ops/db-backup.sh" || fail "db-backup.sh failed — aborting before any change"
else
  log "WARN: ~/ops/db-backup.sh absent; skipping scheduled-style DB backup (relying on hourly)"
fi
UPTAR="$BACKUPS/uploads-genesis-$TS.tar.gz"
log "tarring uploads -> $UPTAR"
tar -C "$SRC_UPLOADS" -czf "$UPTAR" .
TAR_COUNT="$(tar -tzf "$UPTAR" | grep -cv '/$' || true)"
[ "$TAR_COUNT" -ge "$SRC_COUNT" ] || fail "tar file count $TAR_COUNT < live $SRC_COUNT — bad archive"
log "uploads tar OK ($(du -h "$UPTAR" | cut -f1), $TAR_COUNT files)"
# off-site the snapshot too (own prefix, independent of the hourly incremental mirror)
if [ -f "$BASE/ops/r2.env" ] && [ -f "$BASE/ops/r2-upload.py" ] && [ -x "$APP/backend/.venv/bin/python" ]; then
  ( set -a; . "$BASE/ops/r2.env"; set +a
    R2_PREFIX=prod-uploads-snapshots "$APP/backend/.venv/bin/python" "$BASE/ops/r2-upload.py" "$UPTAR" ) \
    && log "uploads snapshot pushed to R2 (prod-uploads-snapshots/)" \
    || log "WARN: R2 snapshot push failed (local tar still OK; hourly mirror still covers uploads)"
else
  log "note: R2 tooling absent — uploads snapshot local-only"
fi

# ─── Phase 1: relocate uploads outside the release tree (root-cause fix) ───
ENV_FILE="$APP/backend/.env"
CUR_PATH="$(grep -E '^STORAGE_LOCAL_PATH=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d '"')"
if [ "$CUR_PATH" = "$SHARED_UPLOADS" ]; then
  log "Phase 1: already done (STORAGE_LOCAL_PATH is $SHARED_UPLOADS) — skipping"
else
  log "Phase 1: moving uploads to shared dir + pointing .env at an absolute path"
  # copy (never move) so the original stays put until we've verified the new path
  if [ ! -d "$SHARED_UPLOADS" ] || [ "$(find "$SHARED_UPLOADS" -type f | wc -l | tr -d ' ')" -lt "$SRC_COUNT" ]; then
    mkdir -p "$SHARED_UPLOADS"
    cp -a "$SRC_UPLOADS/." "$SHARED_UPLOADS/"
  fi
  DST_COUNT="$(find "$SHARED_UPLOADS" -type f | wc -l | tr -d ' ')"
  [ "$DST_COUNT" -ge "$SRC_COUNT" ] || fail "shared copy has $DST_COUNT files < $SRC_COUNT — copy incomplete"
  log "shared uploads ready: $SHARED_UPLOADS ($DST_COUNT files)"
  # flip STORAGE_LOCAL_PATH to the absolute shared path (leave STORAGE_LOCAL_URL as-is)
  cp -a "$ENV_FILE" "$ENV_FILE.pre-genesis-$TS"
  sed -i "s#^STORAGE_LOCAL_PATH=.*#STORAGE_LOCAL_PATH=$SHARED_UPLOADS#" "$ENV_FILE"
  log "STORAGE_LOCAL_PATH -> $SHARED_UPLOADS (.env backed up to .pre-genesis-$TS)"
  sudo systemctl restart "$SERVICE"
  health
  probe_uploads
fi

# ─── Phase 2: convert plain checkout -> blue-green release + symlink ───
if [ -L "$APP" ]; then
  log "Phase 2: already done ($APP -> $(readlink "$APP")) — skipping"
else
  SHA="$(git -C "$APP" rev-parse --short=8 HEAD 2>/dev/null || echo unknown)"
  GENESIS="$RELEASES/genesis-$SHA"
  [ -e "$GENESIS" ] && fail "$GENESIS already exists — refusing to clobber"
  log "Phase 2: $APP (plain dir, $SHA) -> $GENESIS + symlink"
  # same-filesystem rename: atomic, keeps the running uvicorn's cwd/fds valid
  mv "$APP" "$GENESIS"
  ln -sfn "$GENESIS" "$APP"
  log "symlink: $APP -> $(readlink "$APP")"
  sudo systemctl restart "$SERVICE"
  sudo systemctl reload nginx
  health
  probe_uploads
  # frontend must still be served through the symlinked dist
  fe="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8081/ || true)"
  log "frontend/backend root -> $fe"
fi

log "GENESIS CUTOVER COMPLETE"
log "  app:     $APP -> $(readlink -f "$APP")"
log "  uploads: $SHARED_UPLOADS ($(find "$SHARED_UPLOADS" -type f | wc -l | tr -d ' ') files, release-independent)"
log "  rollback (if ever needed): systemctl stop $SERVICE; rm $APP; mv $RELEASES/genesis-* $APP; restore .env.pre-genesis-*; systemctl start $SERVICE"
