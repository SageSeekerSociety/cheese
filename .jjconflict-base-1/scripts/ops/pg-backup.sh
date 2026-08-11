#!/usr/bin/env bash
# Daily PostgreSQL backup for CheeseX production (runs on etrip via cron).
# - pg_dump inside the cheesex-pg container, gzip to /opt/cheesex-data/backups/
# - Keeps 14 days of backups.
# - Logs every run (timestamped) to /opt/cheesex-data/backups/backup.log
# Idempotent: safe to re-run any time; each run produces a new timestamped file.
set -euo pipefail

BACKUP_DIR=/opt/cheesex-data/backups
LOG_FILE="$BACKUP_DIR/backup.log"
RETENTION_DAYS=14
STAMP=$(date +%Y%m%d-%H%M%S)
OUT="$BACKUP_DIR/cheesex-$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG_FILE"; }

log "backup start -> $OUT"
if docker exec cheesex-pg pg_dump -U cheesex cheesex | gzip > "$OUT"; then
  SIZE=$(du -h "$OUT" | cut -f1)
  log "backup ok: $OUT ($SIZE)"
else
  log "backup FAILED (pg_dump exit != 0)"
  rm -f "$OUT"
  exit 1
fi

# prune old backups
DELETED=$(find "$BACKUP_DIR" -name 'cheesex-*.sql.gz' -mtime +"$RETENTION_DAYS" -print -delete | wc -l)
log "pruned $DELETED backups older than $RETENTION_DAYS days"
