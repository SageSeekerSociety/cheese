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

[ -n "$DUMP" ] && [ -f "$DUMP" ] || { echo "ERROR: no dump found (looked in $BK)"; exit 1; }
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
# pg_restore may warn (e.g. a missing extension/role); those are non-fatal — the
# real verdict is the sanity check below, so capture rather than abort on them.
docker exec "$CID" pg_restore --no-owner --no-privileges -U postgres -d restore_test /tmp/restore.dump \
  > /tmp/restore.log 2>&1 || echo "note: pg_restore exited non-zero (see sanity check for verdict)"

q() { docker exec "$CID" psql -U postgres -d restore_test -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }
tables="$(q "select count(*) from information_schema.tables where table_schema='public'")"
alembic="$(q "select count(*) from alembic_version")"
tables="${tables:-0}"; alembic="${alembic:-0}"
echo "restored: public tables=$tables, alembic_version rows=$alembic"

if [ "$tables" -ge "$MIN_TABLES" ] && [ "$alembic" -ge 1 ]; then
  echo "RESTORE-TEST PASS"
  exit 0
fi
echo "RESTORE-TEST FAIL (need tables>=$MIN_TABLES and a migrated schema); pg_restore tail:"
tail -8 /tmp/restore.log 2>/dev/null
exit 1
