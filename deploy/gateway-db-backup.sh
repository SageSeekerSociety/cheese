#!/usr/bin/env bash
# Logical backup of the LLM gateway's database (`litellm-db` in
# deploy/compose/docker-compose.gateway.yml). Run by
# .github/workflows/gateway-db-backup.yml on the dev box; the workflow copies
# the dump off-site with r2-upload.py afterwards.
#
# This database is the only copy of the runtime model rows and their prices
# (STORE_MODEL_IN_DB), the per-project virtual keys and their budgets, and the
# spend logs. Unlike the app database it lives on the same box as these dumps,
# so a dump only survives the box once it is in R2.
#
# Dumps with the container's own pg_dump (same major version as the server),
# writes to a hidden partial file, checks the archive's TOC with the
# container's pg_restore, renames it into place, and keeps the newest
# $GATEWAY_BACKUP_KEEP dumps. Prints the dump's path on stdout; everything else
# goes to stderr and $GATEWAY_BACKUP_DIR/backup.log.
set -euo pipefail

CONTAINER="${GATEWAY_DB_CONTAINER:-cheese-gateway-litellm-db-1}"
DB_USER="${GATEWAY_DB_USER:-litellm}"
DB_NAME="${GATEWAY_DB_NAME:-litellm}"
BACKUP_DIR="${GATEWAY_BACKUP_DIR:-$HOME/backups/gateway}"
KEEP="${GATEWAY_BACKUP_KEEP:-28}"
# The tables whose loss the backup exists for: model rows and virtual keys.
REQUIRED_TABLES=(LiteLLM_ProxyModelTable LiteLLM_VerificationToken)

mkdir -p "$BACKUP_DIR"
LOG="$BACKUP_DIR/backup.log"
log() { echo "$(date -u '+%F %T') $*" | tee -a "$LOG" >&2; }
fail() { log "ERROR: $*"; echo "::error::$*" >&2; exit 1; }

case "$KEEP" in '' | *[!0-9]* | 0) fail "GATEWAY_BACKUP_KEEP must be a positive integer, got '$KEEP'" ;; esac

running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" ||
  fail "container $CONTAINER not found; is the gateway stack up on this box?"
[ "$running" = true ] || fail "container $CONTAINER exists but is not running"

name="gateway-litellm-$(date -u '+%Y%m%d-%H%M%S').dump"
final="$BACKUP_DIR/$name"
partial="$BACKUP_DIR/.$name.partial"
# The workflow's concurrency group means no other run is writing here, so any
# partial left is from a run that was killed before its trap ran.
rm -f "$BACKUP_DIR"/.gateway-litellm-*.dump.partial
trap 'rm -f "$partial"' EXIT

log "starting pg_dump of $DB_NAME in $CONTAINER"
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc --no-owner >"$partial" ||
  fail "pg_dump failed"
[ -s "$partial" ] || fail "pg_dump wrote an empty file"

# A dump that cannot be read back is not a backup.
toc="$(docker exec -i "$CONTAINER" pg_restore --list <"$partial")" ||
  fail "pg_restore cannot read the dump"
tables="$(grep -c ' TABLE DATA ' <<<"$toc" || true)"
[ "$tables" -gt 0 ] || fail "dump lists no table data"
for table in "${REQUIRED_TABLES[@]}"; do
  grep -Eq " TABLE DATA public \"?$table\"? " <<<"$toc" ||
    fail "dump has no data entry for $table"
done

mv -f "$partial" "$final"
log "OK: $final ($(du -h "$final" | cut -f1), $tables tables) verified"

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'gateway-litellm-*.dump' | sort -r |
  tail -n "+$((KEEP + 1))" | while read -r old; do
    rm -f -- "$old"
    log "pruned $old"
  done

echo "$final"
