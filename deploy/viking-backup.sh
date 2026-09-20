#!/usr/bin/env bash
# Scheduled backup of the openviking memory tree (#187).
#
# On MEMORY_BACKEND=openviking that host directory IS a database — the AGFS
# store plus its vector index — and it lives nowhere else: not in Postgres, not
# in the image, not in the uploads mirror. A box rebuild without this job wipes
# every memory the platform has.
#
# Run by a systemd timer (deploy/systemd/cheese-viking-backup.{service,timer}).
# Takes a compressed tar, VERIFIES it is readable, records a freshness marker,
# pushes an off-site copy, and prunes old archives — the same contract as
# db-backup.sh.
#
# Consistency: there is no way to snapshot this tree atomically from outside the
# process that owns it, and stopping the backend for a backup is not on the
# table. So the archive is taken hot, and the tree is fingerprinted before and
# after: if nothing changed while tar ran, the snapshot is coherent and lands as
# cheese-viking-<ts>.tar.gz. If the backend wrote during every attempt, the
# archive is still kept — a possibly-torn copy beats no copy — but it lands as
# cheese-viking-<ts>-hot.tar.gz so whoever restores can see it and prefer a
# quiet one. See deploy/README-backup.md for what "torn" costs you.
#
# ov.conf is deliberately excluded: it holds the plaintext model API keys and
# the backend rewrites it from settings on every start, so backing it up would
# ship secrets off-site to buy nothing.
set -euo pipefail

VIKING_DIR="${VIKING_HOST_PATH:-/home/nictheboy/cheese-viking}"
BACKUP_DIR="${CHEESE_BACKUP_DIR:-/home/nictheboy/backups}"
RETENTION_DAYS="${CHEESE_VIKING_RETENTION_DAYS:-14}"
# How many times to re-take the archive when the tree moves underneath it.
ATTEMPTS="${CHEESE_VIKING_QUIESCE_ATTEMPTS:-3}"
LOG="$BACKUP_DIR/backup.log"

mkdir -p "$BACKUP_DIR"
log() { echo "$(date '+%F %T') viking: $*" | tee -a "$LOG" >&2; }

if [ ! -d "$VIKING_DIR" ]; then
  # deploy-docker.sh creates this dir on every deploy, so its absence means this
  # box does not run the app tier — not that a backup failed.
  log "note: $VIKING_DIR does not exist — nothing to back up"
  exit 0
fi

parent="$(cd "$(dirname "$VIKING_DIR")" && pwd)"
base="$(basename "$VIKING_DIR")"

# Fingerprint of the tree: type, path, size, mtime (sub-second) of every entry
# except the secrets file we never archive. Identical before and after tar means
# nothing was written while we read.
manifest() {
  find "$VIKING_DIR" -mindepth 1 -path "$VIKING_DIR/ov.conf" -prune -o \
    -printf '%y %p %s %T@\n' 2>/dev/null | LC_ALL=C sort
}

if [ -z "$(manifest)" ]; then
  # Empty is the expected steady state while MEMORY_BACKEND=db: the mount is
  # unconditional but nothing writes to it. Not an error, and not worth an
  # archive that says nothing.
  log "note: $VIKING_DIR holds no memory data yet (MEMORY_BACKEND still 'db'?) — skipping"
  exit 0
fi

ts="$(date '+%Y%m%d-%H%M%S')"
stem="cheese-viking-$ts"
TMP="$BACKUP_DIR/$stem.tar.gz.partial"
trap 'rm -f "$TMP"' EXIT

quiesced=0
attempt=1
while [ "$attempt" -le "$ATTEMPTS" ]; do
  before="$(manifest)"
  rm -f "$TMP"
  log "starting tar of $VIKING_DIR (attempt $attempt/$ATTEMPTS)"
  set +e
  tar -czf "$TMP" -C "$parent" --exclude="$base/ov.conf" "$base" 2>>"$LOG"
  rc=$?
  set -e
  # GNU tar: 1 is "a file changed as we read it" (recoverable — retry), 2+ is a
  # real failure and the archive is worthless.
  if [ "$rc" -ge 2 ]; then
    log "ERROR: tar failed (exit $rc)"
    exit 1
  fi
  after="$(manifest)"
  if [ "$rc" -eq 0 ] && [ "$before" = "$after" ]; then
    quiesced=1
    break
  fi
  log "note: the memory tree was written to during attempt $attempt/$ATTEMPTS"
  attempt=$((attempt + 1))
done

if [ "$quiesced" -eq 1 ]; then
  OUT="$BACKUP_DIR/$stem.tar.gz"
else
  OUT="$BACKUP_DIR/$stem-hot.tar.gz"
  log "WARN: no quiet window in $ATTEMPTS attempts — keeping a hot (possibly torn) snapshot"
fi

# An archive you cannot read is not a backup. Listing it decompresses the whole
# gzip stream, so this catches truncation and corruption, not just a bad header.
if ! entries="$(tar -tzf "$TMP" 2>>"$LOG" | wc -l)"; then
  log "ERROR: archive unreadable, discarding"
  exit 1
fi
if [ "$entries" -lt 1 ]; then
  log "ERROR: archive lists no entries, discarding"
  exit 1
fi

mv "$TMP" "$OUT"
trap - EXIT
size="$(du -h "$OUT" | cut -f1)"
log "OK: $OUT ($size, $entries entries) verified"
date +%s > "$BACKUP_DIR/.viking-last-success"

# Off-site copy (3-2-1). Same best-effort contract as db-backup.sh: a missing or
# failing off-site leg is a WARN, never a failed backup.
R2_ENV="${CHEESE_R2_ENV:-/home/nictheboy/ops/r2.env}"
R2_UPLOAD="${CHEESE_R2_UPLOAD:-/home/nictheboy/ops/r2-upload.py}"
VENV_PY="${CHEESE_VENV_PY:-/home/nictheboy/cheese-backend-py/backend/.venv/bin/python}"
if [ -f "$R2_ENV" ] && [ -f "$R2_UPLOAD" ] && [ -x "$VENV_PY" ]; then
  viking_prefix="${CHEESE_VIKING_R2_PREFIX:-viking}"
  # shellcheck disable=SC1090
  set -a; . "$R2_ENV"; set +a
  # r2.env carries the DB dump prefix. Override AFTER sourcing or the file wins
  # and memory archives land in the db/ keyspace.
  export R2_PREFIX="$viking_prefix"
  if "$VENV_PY" "$R2_UPLOAD" "$OUT" >>"$LOG" 2>&1; then
    log "OK: off-site R2 upload done ($R2_PREFIX/)"
    date +%s > "$BACKUP_DIR/.viking-last-offsite-success"
  else
    log "WARN: off-site R2 upload FAILED (local backup still OK)"
  fi
else
  log "note: R2 off-site not configured ($R2_ENV / $R2_UPLOAD missing) — skipping"
fi

# Retention. Each run stores a full copy — there is no incremental leg here — so
# this is the only thing between a growing memory tree and a full disk.
find "$BACKUP_DIR" -maxdepth 1 -name 'cheese-viking-*.tar.gz' -type f -mtime "+$RETENTION_DAYS" \
  -print -delete | while read -r f; do log "pruned old backup $f"; done

log "done"
