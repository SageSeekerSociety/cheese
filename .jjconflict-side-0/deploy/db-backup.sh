#!/usr/bin/env bash
# Scheduled PostgreSQL logical backup for the cheese dev/test DB.
#
# Run by a systemd timer (deploy/systemd/cheese-db-backup.{service,timer}) every
# few hours. Takes a compressed pg_dump, VERIFIES it is readable, records a
# freshness marker, and prunes old backups. The DB lives on a separate host, so
# these dumps already survive a DB-host failure; true off-site (3-2-1) is a
# follow-up once an S3 bucket / remote host is provisioned.
set -euo pipefail

BACKUP_DIR="${CHEESE_BACKUP_DIR:-/home/nictheboy/backups}"
ENV_FILE="${CHEESE_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
RETENTION_DAYS="${CHEESE_BACKUP_RETENTION_DAYS:-30}"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"
log() { echo "$(date '+%F %T') $*" | tee -a "$LOG" >&2; }

[ -f "$ENV_FILE" ] || { log "ERROR: env file not found: $ENV_FILE"; exit 1; }
DBURL="$(grep '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- | sed 's#+asyncpg##; s/^"//; s/"$//')"
[ -n "$DBURL" ] || { log "ERROR: DATABASE_URL empty"; exit 1; }

ts="$(date '+%Y%m%d-%H%M%S')"
OUT="$BACKUP_DIR/cheese-$ts.dump"

log "starting pg_dump -> $OUT"
if ! pg_dump -Fc --no-owner -d "$DBURL" -f "$OUT"; then
  log "ERROR: pg_dump failed"; rm -f "$OUT"; exit 1
fi

# A dump you cannot read is not a backup — verify the archive is intact.
if ! pg_restore --list "$OUT" >/dev/null 2>&1; then
  log "ERROR: dump unreadable, deleting $OUT"; rm -f "$OUT"; exit 1
fi

tables="$(pg_restore --list "$OUT" | grep -c 'TABLE DATA' || true)"
size="$(du -h "$OUT" | cut -f1)"
log "OK: $OUT ($size, ~$tables tables) verified"
date +%s > "$BACKUP_DIR/.last-success"

# Off-site copy (3-2-1): push the verified dump to Cloudflare R2 if configured.
# Credentials live in ~/ops/r2.env (chmod 600, NOT in git). Absent -> skip
# quietly; the local dump on a separate host already counts as a success, so a
# missing/failed off-site leg must not fail the whole backup.
R2_ENV="${CHEESE_R2_ENV:-/home/nictheboy/ops/r2.env}"
R2_UPLOAD="${CHEESE_R2_UPLOAD:-/home/nictheboy/ops/r2-upload.py}"
VENV_PY="${CHEESE_VENV_PY:-/home/nictheboy/cheese-backend-py/backend/.venv/bin/python}"
if [ -f "$R2_ENV" ] && [ -f "$R2_UPLOAD" ] && [ -x "$VENV_PY" ]; then
  # shellcheck disable=SC1090
  set -a; . "$R2_ENV"; set +a
  if "$VENV_PY" "$R2_UPLOAD" "$OUT" >>"$LOG" 2>&1; then
    log "OK: off-site R2 upload done"
    date +%s > "$BACKUP_DIR/.last-offsite-success"
  else
    log "WARN: off-site R2 upload FAILED (local backup still OK)"
  fi
else
  log "note: R2 off-site not configured ($R2_ENV / $R2_UPLOAD missing) — skipping"
fi

# Retention: drop dumps older than RETENTION_DAYS (19MB DB -> pennies of disk).
find "$BACKUP_DIR" -maxdepth 1 -name 'cheese-*.dump' -type f -mtime "+$RETENTION_DAYS" \
  -print -delete | while read -r f; do log "pruned old backup $f"; done

log "done"
