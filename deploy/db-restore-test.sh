#!/usr/bin/env bash
# Prove a backup actually restores — "an untested backup is not a backup".
#
# Restores the latest local pg_dump into a THROWAWAY postgres container, checks
# the schema is migrated and has data, then tears it down. Touches nothing real:
# the ephemeral container is removed on exit. Run by the
# backup-restore-test workflow on a box that has docker + the local dumps.
#
# Usage: db-restore-test.sh [dump-file]   (default: newest ~/backups/cheese-*.dump)
set -uo pipefail

BK="${CHEESE_BACKUP_DIR:-/home/nictheboy/backups}"
DUMP="${1:-$(ls -t "$BK"/cheese-*.dump 2>/dev/null | head -1)}"
MIN_TABLES="${CHEESE_RESTORE_MIN_TABLES:-40}"
ALLOW_EMPTY="${CHEESE_RESTORE_ALLOW_EMPTY:-0}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${CHEESE_RESTORE_LOG_DIR:-$SCRIPT_DIR/../.tmp/restore-tests}"
TEST_DB="restore_$(printf '%s' "$SCRIPT_DIR" | cksum | cut -d' ' -f1)_$$"
# Resolve the server from the same composition as dev, including future pins:
# both the image AND the command. The command has to come from Compose too. The
# image's own entrypoint runs pg_cron, which only loads through
# shared_preload_libraries, so naming pg_search in a `-c` of our own REPLACES
# the composed list (pg_cron,pg_search) instead of adding to it -- the entrypoint
# then fails and the container exits before it ever listens. CHEESE_PG_IMAGE
# remains an explicit experiment override.
PGCMD=()
if [ -n "${CHEESE_PG_IMAGE:-}" ]; then
  PGIMG="$CHEESE_PG_IMAGE"
elif ! COMPOSED="$(docker compose -f "$SCRIPT_DIR/../docker-compose.yml" config \
  --no-interpolate --format json | python3 -c \
  'import json, sys
s = json.load(sys.stdin)["services"]["postgres"]
print(s["image"])
for arg in s.get("command", []):
    print(arg)')"; then
  echo "RESTORE-TEST FAIL: could not resolve the database image from docker-compose.yml"
  exit 1
else
  mapfile -t COMPOSED_LINES <<< "$COMPOSED"
  PGIMG="${COMPOSED_LINES[0]}"
  PGCMD=("${COMPOSED_LINES[@]:1}")
fi

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

# No `--rm`: the container is removed by the trap below, not by Docker on exit.
# A throwaway postgres that dies on startup has to stay inspectable long enough
# for its log to be read — otherwise a dead container and a slow one look the
# same, which is exactly how a broken preload hid behind "never became ready".
if ! CID="$(docker run -d --network none -e POSTGRES_PASSWORD=test \
  -e "POSTGRES_DB=$TEST_DB" "$PGIMG" ${PGCMD[@]+"${PGCMD[@]}"})"; then
  echo "RESTORE-TEST FAIL: could not start throwaway $PGIMG"
  exit 1
fi
cleanup() { docker rm -f "$CID" >/dev/null 2>&1 || true; }
trap cleanup EXIT

# Wait for postgres to accept connections, but stop waiting the moment the
# container is gone: it will not come back, and its own log explains why.
container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$CID" 2>/dev/null)" = "true" ]
}
container_died() {
  container_running && return 1
  echo "RESTORE-TEST FAIL: throwaway postgres exited before it accepted connections; last log lines:"
  docker logs --tail 20 "$CID" 2>&1 || true
  exit 1
}
ready=0
for _ in $(seq 1 30); do
  container_died
  if docker exec "$CID" pg_isready -h 127.0.0.1 -U postgres -d "$TEST_DB" >/dev/null 2>&1; then ready=1; break; fi
  sleep 1
done
if [ "$ready" != 1 ]; then
  container_died
  echo "RESTORE-TEST FAIL: throwaway postgres never became ready within 30s; last log lines:"
  docker logs --tail 20 "$CID" 2>&1 || true
  exit 1
fi

if ! docker cp "$DUMP" "$CID":/tmp/restore.dump >> "$RESTORE_LOG" 2>&1; then
  echo "RESTORE-TEST FAIL: could not copy dump; log retained at $RESTORE_LOG"
  exit 1
fi
# A fixture without the extension would let a wrong image pass unnoticed.
if ! docker exec "$CID" pg_restore --list /tmp/restore.dump > "$RESTORE_LOG.toc" 2>> "$RESTORE_LOG"; then
  echo "RESTORE-TEST FAIL: could not list dump; log retained at $RESTORE_LOG"
  exit 1
fi
if ! grep -Eq ' EXTENSION - pg_search([[:space:]]|$)' "$RESTORE_LOG.toc"; then
  echo "RESTORE-TEST FAIL: dump does not contain the pg_search extension"
  exit 1
fi
echo "dump contains pg_search; restoring with preload enabled"
# --clean --if-exists, as production restores (see README-backup.md): the
# throwaway database is initialised by the image's own entrypoint, which already
# loads pg_search and creates the paradedb schema -- the same objects the dump
# carries. Without --clean the first `CREATE SCHEMA paradedb` collides with the
# pre-created one and the restore aborts.
#
# A partial restore can contain enough schema to fool every query below. The
# restore process result is therefore authoritative; warnings remain in the log.
restore_status=0
docker exec "$CID" pg_restore --exit-on-error --clean --if-exists \
  --no-owner --no-privileges -U postgres -d "$TEST_DB" /tmp/restore.dump \
  > "$RESTORE_LOG" 2>&1 \
  || restore_status=$?
if [ "$restore_status" -ne 0 ]; then
  echo "RESTORE-TEST FAIL: pg_restore exited $restore_status; log retained at $RESTORE_LOG"
  tail -20 "$RESTORE_LOG" 2>/dev/null
  exit 1
fi

q() {
  docker exec "$CID" psql -U postgres -d "$TEST_DB" -tAc "$1" \
    2>> "$RESTORE_LOG" | tr -d '[:space:]'
}
query_failed() {
  echo "RESTORE-TEST FAIL: could not query $1; log retained at $RESTORE_LOG"
  tail -20 "$RESTORE_LOG" 2>/dev/null
  exit 1
}

if ! extension="$(q "select count(*) from pg_extension where extname='pg_search'")"; then
  query_failed "pg_search extension"
fi
if [ "$extension" != 1 ]; then
  echo "RESTORE-TEST FAIL: pg_search was not restored"
  exit 1
fi
echo "restored pg_search extension"

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
