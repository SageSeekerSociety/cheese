#!/usr/bin/env bash
# Scheduled backup for the etrip box (Aliyun HK, Dockerized cheese-backend-py +
# cheesex agent stack). Dumps both Postgres containers, tars the uploads volume,
# verifies, ships off-site to Cloudflare R2 (etrip/ prefix), prunes old local
# copies. Run by cheese-etrip-backup.timer (systemd, as root).
set -uo pipefail
BK=/root/backups; mkdir -p "$BK"; LOG="$BK/backup.log"
R2ENV=/root/ops/r2.env; UPLOADER=/root/ops/r2-upload.py
RET_DAYS=${ETRIP_BACKUP_RETENTION_DAYS:-14}
log(){ echo "$(date '+%F %T') $*" | tee -a "$LOG" >&2; }
TS=$(date +%Y%m%d-%H%M%S); rc=0; ARTIFACTS=()

dump_db(){ # container user db
  local c=$1 u=$2 db=$3
  local out="$BK/etrip-${db}-db-$TS.dump"
  if docker exec "$c" bash -c "pg_dump -U $u -Fc --no-owner '$db' -f /tmp/_bk.dump && pg_restore --list /tmp/_bk.dump >/dev/null"; then
    docker cp "$c":/tmp/_bk.dump "$out" >/dev/null; docker exec "$c" rm -f /tmp/_bk.dump || true
    log "OK db '$db' -> $(basename "$out") ($(du -h "$out"|cut -f1))"; ARTIFACTS+=("$out")
  else
    log "ERROR: dump '$db' from $c failed"; rc=1
  fi
}
dump_db cheese_prod_postgres postgres cheese
dump_db cheesex-pg cheesex cheesex

TAR="$BK/etrip-uploads-$TS.tar"
if tar cf "$TAR" -C /var/lib/docker/volumes/uploads _data 2>/dev/null; then
  log "OK uploads -> $(basename "$TAR") ($(du -h "$TAR"|cut -f1), $(tar tf "$TAR"|grep -cv '/$') files)"; ARTIFACTS+=("$TAR")
else
  log "ERROR: uploads tar failed"; rc=1
fi

if [ -f "$R2ENV" ] && [ -f "$UPLOADER" ] && [ "${#ARTIFACTS[@]}" -gt 0 ]; then
  set -a; . "$R2ENV"; set +a
  for a in "${ARTIFACTS[@]}"; do
    if python3 "$UPLOADER" "$a" >>"$LOG" 2>&1; then log "OK off-site $(basename "$a")"; else log "WARN: off-site upload failed for $(basename "$a")"; rc=1; fi
  done
  [ "$rc" = 0 ] && date +%s > "$BK/.last-offsite-success"
else
  log "note: R2 not configured, skipping off-site"
fi
date +%s > "$BK/.last-success"
find "$BK" -maxdepth 1 -name 'etrip-*' -type f -mtime "+$RET_DAYS" -print -delete | while read -r f; do log "pruned $f"; done
log "done (rc=$rc)"; exit "$rc"
