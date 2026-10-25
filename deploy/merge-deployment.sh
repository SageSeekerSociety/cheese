#!/usr/bin/env bash
# Merges another Cheese deployment's export into the deployment on this box, or
# takes one merged batch back. .github/workflows/merge-deployment.yml runs it on
# the box's runner; deploy/README-merge-deployment.md says what an export is.
#
#   merge-deployment.sh dry-run|import <batch> <export-dir>
#   merge-deployment.sh revert-dry-run|revert <batch>
#
# Everything runs beside the running backend: its image (so the merge code and
# the migrations are this release's), its environment, its volumes and its
# network. For dry-run and import it
#   1. restores <export-dir>/database.dump into a scratch Postgres that lives
#      only for this run, and records which columns each of its tables had;
#   2. migrates that copy to this release's head, with this deployment's
#      DATA_ENCRYPTION_KEY first, so every secret in it is sealed under this
#      deployment's key (the export's secrets.env supplies the source's own
#      keys to open them); new blobs a migration writes stay in the scratch
#      container, and its uploads are read from the export, read-only;
#   3. runs scripts/merge_deployment.py against it.
# An import and a revert take a verified backup (deploy/db-backup.sh) first.
# The log of each run is kept in the backup directory.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ACTION="${1:?usage: merge-deployment.sh dry-run|import|revert-dry-run|revert <batch> [export-dir]}"
BATCH="${2:?a batch name is required}"
EXPORT="${3:-}"
[[ "$BATCH" =~ ^[A-Za-z0-9._-]{1,100}$ ]] || { echo "batch must be 1-100 of [A-Za-z0-9._-]"; exit 2; }

BACKUP_DIR="${CHEESE_BACKUP_DIR:-$HOME/backups}"
mkdir -p "$BACKUP_DIR"
LOG="$BACKUP_DIR/merge-deployment-$BATCH-$ACTION-$(date -u +%Y%m%dT%H%M%SZ).log"
exec > >(trap '' INT TERM HUP PIPE; exec tee -a "$LOG") 2>&1
say() { echo "[$(date -u +%FT%TZ)] $*"; }
say "log: $LOG"

# The scratch Postgres is the version the platform's database runs.
SCRATCH_IMAGE=paradedb/paradedb:v0.24.0-pg17@sha256:663ecc6dac5165ae2a664c7bd16fb8d8970867e89006ae4f6aa9cd26b1a2a3a4
SCRATCH_PORT=55432

BACKEND="$(bash "$HERE/app-container.sh" backend)" || { say "no backend is running here"; exit 1; }
IMAGE="$(docker inspect -f '{{.Image}}' "$BACKEND")"
say "beside $BACKEND, image $IMAGE"

WORK="$(mktemp -d)"
chmod 700 "$WORK"
SCRATCH=""
cleanup() {
  [ -n "$SCRATCH" ] && docker rm -f "$SCRATCH" >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap cleanup EXIT

# The backend's own environment, secrets included: written only into WORK.
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$BACKEND" | grep -v '^$' > "$WORK/backend.env"
TARGET_KEY="$(sed -n 's/^DATA_ENCRYPTION_KEY=//p' "$WORK/backend.env")"
[ -n "$TARGET_KEY" ] || { say "the backend has no DATA_ENCRYPTION_KEY"; exit 1; }

beside() {
  docker run --rm --network "container:$BACKEND" --env-file "$WORK/backend.env" "$@"
}

case "$ACTION" in
  revert-dry-run|revert)
    if [ "$ACTION" = revert ]; then
      say "backup before the revert"
      bash "$HERE/db-backup.sh"
      flag=()
    else
      flag=(--dry-run)
    fi
    beside --volumes-from "$BACKEND" "$IMAGE" \
      python -m scripts.merge_deployment revert --batch "$BATCH" "${flag[@]}"
    exit
    ;;
  dry-run|import) ;;
  *) say "unknown action $ACTION"; exit 2 ;;
esac

[ -n "$EXPORT" ] || { say "an export directory is required"; exit 2; }
EXPORT="$(cd "$EXPORT" && pwd)"
[ -f "$EXPORT/database.dump" ] || { say "$EXPORT/database.dump is missing"; exit 2; }
say "export $EXPORT: database.dump $(du -h "$EXPORT/database.dump" | cut -f1)$( [ -d "$EXPORT/uploads" ] && echo ", uploads $(find "$EXPORT/uploads" -type f | wc -l) files")"

# The source's own keys, to open its secrets: REALNAME_ENCRYPTION_KEY or
# JWT_SECRET for a source from before secrets moved under DATA_ENCRYPTION_KEY,
# SOURCE_DATA_ENCRYPTION_KEY for one after. Kept on the box, never in the repo.
SOURCE_KEY=""
source_env=()
if [ -f "$EXPORT/secrets.env" ]; then
  SOURCE_KEY="$(sed -n 's/^SOURCE_DATA_ENCRYPTION_KEY=//p' "$EXPORT/secrets.env")"
  while IFS= read -r line; do
    case "$line" in
      REALNAME_ENCRYPTION_KEY=*|JWT_SECRET=*) source_env+=(-e "$line") ;;
    esac
  done < "$EXPORT/secrets.env"
fi
KEYRING="$TARGET_KEY${SOURCE_KEY:+,$SOURCE_KEY}"

SCRATCH="cheese-merge-source-$BATCH-$$"
PW="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
say "scratch database $SCRATCH"
docker run -d --name "$SCRATCH" --network "container:$BACKEND" \
  -e POSTGRES_PASSWORD="$PW" -v "$EXPORT:/export:ro" \
  "$SCRATCH_IMAGE" -c port="$SCRATCH_PORT" >/dev/null
for _ in $(seq 1 60); do
  docker exec "$SCRATCH" pg_isready -q -p "$SCRATCH_PORT" -U postgres && break
  sleep 1
done
docker exec "$SCRATCH" pg_isready -q -p "$SCRATCH_PORT" -U postgres || { say "scratch database did not start"; exit 1; }
psql_scratch() { docker exec -i "$SCRATCH" psql -p "$SCRATCH_PORT" -U postgres -X -v ON_ERROR_STOP=1 -qAt "$@"; }
psql_scratch -c "CREATE DATABASE source"
# --no-owner: everything belongs to the scratch superuser, which is all there is.
docker exec "$SCRATCH" pg_restore -p "$SCRATCH_PORT" -U postgres -d source --no-owner --no-acl /export/database.dump \
  || say "pg_restore reported errors (above); the checks below decide"
say "source restored at $(psql_scratch -d source -c 'SELECT version_num FROM alembic_version')"

psql_scratch -d source -c "SELECT coalesce(json_object_agg(table_name, cols), '{}') FROM (
  SELECT table_name, json_agg(column_name ORDER BY ordinal_position) AS cols
  FROM information_schema.columns WHERE table_schema = 'public' GROUP BY table_name) x" \
  > "$WORK/source-columns.json"
chmod 644 "$WORK/source-columns.json"

SOURCE_URL="postgresql://postgres:$PW@127.0.0.1:$SCRATCH_PORT/source"
say "migrating the source copy to this release's head"
beside -e "DATABASE_URL=postgresql+asyncpg://postgres:$PW@127.0.0.1:$SCRATCH_PORT/source" \
  -e "DATA_ENCRYPTION_KEY=$KEYRING" "${source_env[@]}" \
  -e FILES_STORE=local -e WORKSPACE_ROOT=/tmp/merge-scratch \
  -e STORAGE_TYPE=local -e STORAGE_LOCAL_PATH=/export/uploads \
  -v "$EXPORT:/export:ro" "$IMAGE" alembic upgrade head
say "source copy at $(psql_scratch -d source -c 'SELECT version_num FROM alembic_version')"

args=(import --batch "$BATCH" --source-url "$SOURCE_URL" --source-columns /merge/source-columns.json)
[ -d "$EXPORT/uploads" ] && args+=(--source-uploads /export/uploads)
[ -f "$EXPORT/manifest.json" ] && args+=(--manifest /export/manifest.json)
if [ "$ACTION" = dry-run ]; then
  args+=(--dry-run)
else
  say "backup before the import"
  bash "$HERE/db-backup.sh"
fi
beside --volumes-from "$BACKEND" -e "DATA_ENCRYPTION_KEY=$KEYRING" \
  -v "$EXPORT:/export:ro" -v "$WORK/source-columns.json:/merge/source-columns.json:ro" \
  "$IMAGE" python -m scripts.merge_deployment "${args[@]}"
say "done"
