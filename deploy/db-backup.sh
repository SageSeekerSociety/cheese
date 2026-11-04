#!/usr/bin/env bash
# Scheduled PostgreSQL logical backup for the cheese dev/test DB.
#
# Run hourly by deploy/systemd/cheese-db-backup.{service,timer}. Verify each
# local dump, upload when the last R2 success is old enough, then prune locally.
set -euo pipefail

# Box-local settings. dev sets CHEESE_BACKUP_HOURLY_DAYS here; a box without
# the file (prod) keeps the plain RETENTION_DAYS prune below.
BACKUP_ENV="${CHEESE_BACKUP_ENV:-/home/nictheboy/ops/backup.env}"
if [ -f "$BACKUP_ENV" ]; then
  # shellcheck disable=SC1090
  set -a; . "$BACKUP_ENV"; set +a
fi

BACKUP_DIR="${CHEESE_BACKUP_DIR:-/home/nictheboy/backups}"
ENV_FILE="${CHEESE_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
RETENTION_DAYS="${CHEESE_BACKUP_RETENTION_DAYS:-30}"
HOURLY_DAYS="${CHEESE_BACKUP_HOURLY_DAYS:-}"
OFFSITE_INTERVAL_HOURS="${CHEESE_BACKUP_OFFSITE_INTERVAL_HOURS:-6}"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"
log() { echo "$(date '+%F %T') $*" | tee -a "$LOG" >&2; }

if ! [[ "$OFFSITE_INTERVAL_HOURS" =~ ^[1-9][0-9]*$ ]]; then
  log "ERROR: CHEESE_BACKUP_OFFSITE_INTERVAL_HOURS must be a positive integer"; exit 1
fi

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
now="$(date +%s)"
last_offsite="$(cat "$BACKUP_DIR/.last-offsite-success" 2>/dev/null || true)"
offsite_due=1
if [[ "$last_offsite" =~ ^[0-9]+$ ]]; then
  offsite_age=$((now - last_offsite))
  if [ "$offsite_age" -ge 0 ] && [ "$offsite_age" -lt $((OFFSITE_INTERVAL_HOURS * 3600)) ]; then
    offsite_due=0
  fi
fi
if [ "$offsite_due" = 0 ]; then
  log "note: off-site R2 upload skipped (not due: age=${offsite_age}s, interval=$((OFFSITE_INTERVAL_HOURS * 3600))s)"
elif [ -f "$R2_ENV" ] && [ -f "$R2_UPLOAD" ] && [ -x "$VENV_PY" ]; then
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

# Retention. With HOURLY_DAYS set: every dump for HOURLY_DAYS, then one per day
# to RETENTION_DAYS. Local retention is independent of the R2 upload cadence;
# pruning must not upload the intentionally local hourly dumps. The plain
# RETENTION_DAYS prune below runs either way, so
# a pruner that fails cannot let the disk fill past the horizon.
R2_PRUNE="${CHEESE_R2_PRUNE:-/home/nictheboy/ops/r2-prune-dumps.py}"
prune_failed=0
if [ -n "$HOURLY_DAYS" ]; then
  status=0
  "$VENV_PY" "$R2_PRUNE" "$BACKUP_DIR" --hourly-days "$HOURLY_DAYS" \
    --retention-days "$RETENTION_DAYS" --local-only >>"$LOG" 2>&1 || status=$?
  case "$status" in
    0) log "OK: tiered prune done" ;;
    3) log "WARN: tiered prune kept dumps without a verified daily copy" ;;
    *) log "ERROR: tiered prune failed (exit $status)"; prune_failed=1 ;;
  esac
fi
find "$BACKUP_DIR" -maxdepth 1 -name 'cheese-*.dump' -type f -mtime "+$RETENTION_DAYS" \
  -print -delete | while read -r f; do log "pruned old backup $f"; done

log "done"
# The dump above is good (.last-success is written); a failed pruner still
# fails the unit so `systemctl --failed` shows it.
exit "$prune_failed"
