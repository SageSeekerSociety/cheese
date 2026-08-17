#!/usr/bin/env bash
# Automated blue-green deploy for the cheese bare-metal boxes — the dev/test box
# (cheese-dev-env1-app, auto on merge) and prod (RUC) (cheese-prod-app, gated by
# a release + human approval). Both boxes share identical paths, service name and
# health port, so the same script drives both; only the CI trigger differs.
#
# Invoked by a self-hosted GitHub Actions runner (user nictheboy) — see
# .github/workflows/deploy-dev.yml and deploy-prod.yml — or by hand. Automates the
# manual runbook: fresh release dir -> deps -> frontend build -> migrate (backup
# first) -> atomic symlink swap -> restart -> health check -> auto-rollback.
#
# Requires a permanent swapfile on the box (scripts/ops/setup-permanent-swap.sh)
# so the vite build has headroom; provisioned once, not per deploy.
#
# Usage: deploy-blue-green.sh <ref-or-sha>   (default: main)
set -euo pipefail

REF="${1:-main}"
BASE="/home/nictheboy"
RELEASES="$BASE/releases"
LINK="$BASE/cheese-backend-py"
REPO="https://github.com/SageSeekerSociety/cheese.git"
HEALTH_URL="http://127.0.0.1:8081/healthz"
SERVICE="cheese-backend-py.service"

log() { echo "[deploy $(date '+%H:%M:%S')] $*"; }
fail() { echo "[deploy $(date '+%H:%M:%S')] ERROR: $*" >&2; exit 1; }

# --- toolchain (the runner's job PATH is minimal) ---
export PATH="$BASE/.local/bin:$PATH"
export PNPM_HOME="$BASE/.local/share/pnpm"
export PATH="$PNPM_HOME:$PATH"
export NVM_DIR="$BASE/.nvm"
# shellcheck disable=SC1091
[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"
nvm use default >/dev/null 2>&1 || true
command -v uv >/dev/null || fail "uv not found on PATH"
command -v pnpm >/dev/null || fail "pnpm not found on PATH"
command -v node >/dev/null || fail "node not found on PATH"

OLD="$(readlink -f "$LINK" 2>/dev/null || true)"
SHA="$(git ls-remote "$REPO" "$REF" 2>/dev/null | awk 'NR==1{print $1}')"
[ -z "$SHA" ] && SHA="$REF" # a raw commit SHA was passed (e.g. github.sha)
SHORT="${SHA:0:8}"
NEW="$RELEASES/ci-$(date '+%Y%m%d-%H%M%S')-$SHORT"
log "ref=$REF sha=$SHORT"
log "old=$OLD"
log "new=$NEW"

# --- 1. fetch target code into a fresh release dir ---
git clone --quiet "$REPO" "$NEW"
git -C "$NEW" checkout --quiet "$SHA" || fail "checkout $SHA failed"

# --- 2. carry env forward from the live release (never regenerate secrets) ---
cp "$LINK/backend/.env" "$NEW/backend/.env"
[ -f "$LINK/frontend/.env" ] && cp "$LINK/frontend/.env" "$NEW/frontend/.env"

# --- 3. backend deps ---
log "uv sync --frozen"
( cd "$NEW/backend" && uv sync --frozen --no-dev )

# --- 4. frontend build (relies on the box's permanent swap for vite headroom) ---
log "pnpm install + build"
( cd "$NEW/frontend" \
    && pnpm install --frozen-lockfile \
    && NODE_OPTIONS="--max-old-space-size=6144" pnpm build )
[ -f "$NEW/frontend/dist/index.html" ] || fail "frontend build produced no dist/index.html"

# --- 5. migrate (back up the DB first, only if migrations are pending) ---
ALEMBIC="$NEW/backend/.venv/bin/alembic"
cur="$(cd "$NEW/backend" && "$ALEMBIC" current 2>/dev/null | awk 'NR==1{print $1}')"
head="$(cd "$NEW/backend" && "$ALEMBIC" heads 2>/dev/null | awk 'NR==1{print $1}')"
if [ "$cur" != "$head" ]; then
  log "migrations pending: $cur -> $head — backing up DB first"
  mkdir -p "$BASE/backups"
  DBURL="$(grep '^DATABASE_URL=' "$NEW/backend/.env" | cut -d= -f2- | sed 's#+asyncpg##; s/^"//; s/"$//')"
  DUMP="$BASE/backups/predeploy-$(date '+%Y%m%d-%H%M%S')-$SHORT.dump"
  pg_dump -Fc -d "$DBURL" -f "$DUMP" || fail "DB backup failed — aborting before migrate"
  log "backup ok: $DUMP"
  ( cd "$NEW/backend" && "$ALEMBIC" upgrade head ) || fail "alembic upgrade failed"
else
  log "migrations up to date ($cur)"
fi

# --- 6. atomic symlink swap + restart ---
ln -sfn "$NEW" "$LINK"
log "symlink -> $(readlink "$LINK")"
sudo systemctl restart "$SERVICE"
sudo systemctl reload nginx

# --- 7. health check, auto-rollback on failure ---
code=""
for _ in $(seq 1 10); do
  sleep 2
  code="$(curl -s -o /dev/null -w '%{http_code}' "$HEALTH_URL" || true)"
  [ "$code" = 200 ] && break
done
if [ "$code" != 200 ]; then
  log "HEALTH CHECK FAILED (last=$code)"
  if [ -n "$OLD" ] && [ -d "$OLD" ]; then
    log "rolling back symlink -> $OLD"
    ln -sfn "$OLD" "$LINK"
    sudo systemctl restart "$SERVICE"
    sudo systemctl reload nginx
  fi
  fail "deploy failed health check, rolled back"
fi

log "DEPLOY OK: $LINK -> $(readlink "$LINK") (health 200)"
# prune old ci- releases, keep the 5 most recent (plus whatever OLD/tagged dirs)
ls -1dt "$RELEASES"/ci-* 2>/dev/null | tail -n +6 | while read -r d; do
  [ "$d" != "$OLD" ] && rm -rf "$d" && log "pruned old release $d"
done
