#!/usr/bin/env bash
# Prove a backup actually restores — "an untested backup is not a backup".
#
# Restores the latest local pg_dump into a THROWAWAY postgres container, checks
# the schema is migrated and has data, then tears it down. Touches nothing real:
# the ephemeral container is `--rm` and stopped on exit. Run by the
# backup-restore-test workflow on a box that has docker + the local dumps.
#
# Usage: db-restore-test.sh [dump-file]   (default: newest ~/backups/cheese-*.dump)
set -uo pipefail

BK="${CHEESE_BACKUP_DIR:-/home/nictheboy/backups}"
DUMP="${1:-$(ls -t "$BK"/cheese-*.dump 2>/dev/null | head -1)}"
PGIMG="${CHEESE_PG_IMAGE:-postgres:17}"
MIN_TABLES="${CHEESE_RESTORE_MIN_TABLES:-40}"
ALLOW_EMPTY="${CHEESE_RESTORE_ALLOW_EMPTY:-0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${CHEESE_RESTORE_LOG_DIR:-$SCRIPT_DIR/../tmp/restore-tests}"

case "$MIN_TABLES" in
  ''|*[!0-9]*) echo "ERROR: CHEESE_RESTORE_MIN_TABLES must be a non-negative integer"; exit 1 ;;
esac
case "$ALLOW_EMPTY" in
  0|1) ;;
  *) echo "ERROR: CHEESE_RESTORE_ALLOW_EMPTY must be 0 or 1"; exit 1 ;;
esac

[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "ERROR: no dump found (looked in $BK)"; exit 1; }
mkdir -p "$LOG_DIR"
RESTORE_LOG="$LOG_DIR/restore-$(date -u '+%Y%m%dT%H%M%SZ')-$$.log"
echo "restore-testing $DUMP ($(du -h "$DUMP" | cut -f1)) into throwaway $PGIMG"

CID="$(docker run -d --rm -e POSTGRES_PASSWORD=test -e POSTGRES_DB=restore_test "$PGIMG")"
cleanup() { docker stop "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# wait for the container's postgres to accept connections
ready=0
for _ in $(seq 1 30); do
  if docker exec "$CID" pg_isready -U postgres >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
[ "$ready" = 1 ] || { echo "ERROR: throwaway postgres never became ready"; exit 1; }

docker cp "$DUMP" "$CID":/tmp/restore.dump >/dev/null
# A partial restore can contain enough schema to fool every query below. The
# restore process result is therefore authoritative; warnings remain in the log.
restore_status=0
docker exec "$CID" pg_restore --exit-on-error --no-owner --no-privileges \
  -U postgres -d restore_test /tmp/restore.dump > "$RESTORE_LOG" 2>&1 \
  || restore_status=$?
if [ "$restore_status" -ne 0 ]; then
  echo "RESTORE-TEST FAIL: pg_restore exited $restore_status; log retained at $RESTORE_LOG"
  tail -20 "$RESTORE_LOG" 2>/dev/null
  exit 1
fi

q() {
  docker exec "$CID" psql -U postgres -d restore_test -tAc "$1" \
    2>> "$RESTORE_LOG" | tr -d '[:space:]'
}
query_failed() {
  echo "RESTORE-TEST FAIL: could not query $1; log retained at $RESTORE_LOG"
  tail -20 "$RESTORE_LOG" 2>/dev/null
  exit 1
}

if ! tables="$(q \
  "select count(*) from information_schema.tables where table_schema='public'")"; then
  query_failed "public table count"
fi
if ! alembic="$(q "select count(*) from alembic_version")"; then
  query_failed "migration state"
fi
if ! business_rows="$(q '
  select
    (select count(*) from "user")
    + (select count(*) from projects)
    + (select count(*) from topics)
    + (select count(*) from blocks)
')"; then
  query_failed "application data"
fi
echo "restored: public tables=$tables, alembic_version rows=$alembic, critical business rows=$business_rows"

data_ok=0
if [ "$business_rows" -ge 1 ]; then
  data_ok=1
elif [ "$ALLOW_EMPTY" = 1 ]; then
  data_ok=1
  echo "note: empty application data accepted by CHEESE_RESTORE_ALLOW_EMPTY=1"
fi

if [ "$tables" -ge "$MIN_TABLES" ] && [ "$alembic" -ge 1 ] && [ "$data_ok" -eq 1 ]; then
  echo "RESTORE-TEST PASS"
  exit 0
fi
echo "RESTORE-TEST FAIL (need tables>=$MIN_TABLES, a migrated schema, and application data); pg_restore tail:"
tail -8 "$RESTORE_LOG" 2>/dev/null
exit 1
