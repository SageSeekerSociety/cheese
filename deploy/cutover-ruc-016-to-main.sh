#!/usr/bin/env bash
# Moves the RUC production database (cheese.ruc.edu.cn) off the 0.16.x
# release line onto main's migration chain. deploy-prod.yml runs it on the prod
# box right before deploy-docker.sh, whose `alembic upgrade head` finishes the
# move. It acts only while the database is at 0.16.x's head (b1c2d3e4f5a6) and
# the release being deployed is on main's chain; otherwise it says why and
# exits 0, changing nothing.
#
#   cutover-ruc-016-to-main.sh [--dry-run] <image-tag>
#
# --dry-run runs every check below that changes nothing, then stops.
#
# In order, stopping at the first failure:
#   checks   the release image is on main's chain and accepts the box's backend
#            env; the superuser logs in; pg_search is preloaded
#   stop     the 0.16.x backend, so the backup and the archive are final
#   backup   deploy/db-backup.sh: a verified dump, with its R2 copy
#   utf8     deploy/cutover-sqlascii-to-utf8.sh, while still SQL_ASCII
#   archive  every table the bridge or main's chain drops, to a dump beside
#            the backups (taken after the rebuild, so it restores as UTF8)
#   bridge   deploy/cutover-ruc-016-to-main.sql, one transaction
#   retire   remove the stopped 0.16.x backend container: it cannot run on the
#            bridged database, so deploy-docker.sh must not roll back to it
# A failure before the bridge commits starts the 0.16.x backend again.
#
# The superuser comes from CHEESE_DB_ADMIN_ENV (default
# /home/nictheboy/ops/db-admin.env, chmod 600): PGHOST, PGPORT, PGUSER,
# PGPASSWORD for a superuser that pg_hba admits from this box to the databases
# postgres, cheese and cheese_utf8.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=0
if [ "${1:-}" = --dry-run ]; then DRY_RUN=1; shift; fi
TAG="${1:?usage: cutover-ruc-016-to-main.sh [--dry-run] <image-tag>}"

ENV_FILE="${CHEESE_ENV_FILE:-/home/nictheboy/cheese-backend-py/backend/.env}"
BACKUP_DIR="${CHEESE_BACKUP_DIR:-/home/nictheboy/backups}"
ADMIN_ENV="${CHEESE_DB_ADMIN_ENV:-/home/nictheboy/ops/db-admin.env}"
IMAGE="${BACKEND_IMAGE:-ghcr.io/sageseekersociety/cheese/backend:$TAG}"
OLD_HEAD=b1c2d3e4f5a6
BRIDGE_REV=f6b7c8d9e0a1
# What the bridge (0.16.x agent layer) and main's chain after it drop. topic and
# its two relations become main's tag tables; the archive keeps the originals.
ARCHIVE_TABLES="agent_screen block device device_auth_code device_project document
  message_reaction thread thread_membership thread_membership_application
  ai_conversation ai_message task_ai_advice task_ai_advice_context knowledge
  knowledge_label knowledge_upvote project project_membership user_ai_quota
  question_topic_relation topic task_topics_relation space_classification_topics_relation"

mkdir -p "$BACKUP_DIR"
TS="$(date '+%Y%m%d-%H%M%S')"
LOG="$BACKUP_DIR/cutover-ruc-016-$TS.log"
exec > >(trap '' INT TERM HUP PIPE; exec tee -a "$LOG") 2>&1
log() { echo "$(date '+%F %T') $*"; }
fail() { log "ERROR: $*"; exit 1; }

[ -f "$ENV_FILE" ] || fail "backend env file not found: $ENV_FILE"
DBURL="$(grep '^DATABASE_URL=' "$ENV_FILE" | cut -d= -f2- | sed 's#+asyncpg##; s/^"//; s/"$//')"
[ -n "$DBURL" ] || fail "DATABASE_URL empty in $ENV_FILE"
app_sql() { psql "$DBURL" -X -v ON_ERROR_STOP=1 -tA "$@"; }
# shellcheck disable=SC1090 # a box-local file, not in the repo
admin() { ( set -a; . "$ADMIN_ENV"; set +a; "$@" ); }
admin_sql() { admin psql -X -v ON_ERROR_STOP=1 -tA "$@"; }
version() { app_sql -c "select string_agg(version_num, ',') from alembic_version"; }

log "log: $LOG"
VERSION="$(version)" || fail "cannot read alembic_version as the app role"
if [ "$VERSION" != "$OLD_HEAD" ]; then
  log "database is at $VERSION, not 0.16.x's head $OLD_HEAD: no cutover"
  exit 0
fi

log "database is at 0.16.x's head; checking release $IMAGE"
docker pull -q "$IMAGE" >/dev/null || fail "could not pull $IMAGE"
# Which chain the release migrates along. 0.16.x branched off main's chain at
# BRIDGE_REV, so a 0.16.x release knows that revision too; only OLD_HEAD tells
# them apart, and a failed run is a failure, never "not 0.16.x".
CHAIN="$(docker run --rm --entrypoint python "$IMAGE" -c '
from alembic.config import Config
from alembic.script import ScriptDirectory
ids = {r.revision for r in ScriptDirectory.from_config(Config("alembic.ini")).walk_revisions()}
print("0.16" if "'"$OLD_HEAD"'" in ids else "main" if "'"$BRIDGE_REV"'" in ids else "neither")
')" || fail "could not read the migration chain of $IMAGE"
case "$CHAIN" in
  0.16) log "release $TAG is itself on the 0.16.x chain: no cutover"; exit 0 ;;
  main) ;;
  *) fail "release $TAG knows neither $OLD_HEAD nor $BRIDGE_REV; it cannot migrate this database" ;;
esac
# Main's Settings refuse to boot on an env 0.16.x accepted (CORS_ORIGINS as a
# JSON list, DATA_ENCRYPTION_KEY, PLATFORM_ADMIN_HANDLES). Found after the
# bridge, that would leave the site down with no release able to run. A
# pydantic error's text carries the input values, secrets included, and this
# log is the Actions log, so only each error's field and reason are printed.
settings_check='
import sys
from pydantic import ValidationError
try:
    import app.core.config  # noqa: F401  (builds Settings from /app/.env)
except ValidationError as e:
    for err in e.errors(include_input=False, include_url=False):
        print("settings:", ".".join(map(str, err["loc"])) or "(model)", "-", err["msg"])
    sys.exit(1)
'
docker run --rm --user "$(id -u):$(id -g)" -e DEPLOYED_VIA_COMPOSE=1 \
  -v "$ENV_FILE:/app/.env:ro" --entrypoint python "$IMAGE" -c "$settings_check" \
  || fail "release $TAG does not accept $ENV_FILE (its reasons are above); fix the env file first"
log "release $TAG is on main's chain and accepts $ENV_FILE"

[ -f "$ADMIN_ENV" ] || fail "no superuser login: $ADMIN_ENV is missing (PGHOST, PGPORT, PGUSER, PGPASSWORD)"
[ "$(admin_sql -d postgres -c 'select rolsuper from pg_roles where rolname = current_user')" = t ] \
  || fail "the login in $ADMIN_ENV is not a superuser"
admin_sql -d cheese -c 'select 1' >/dev/null || fail "pg_hba does not admit the superuser to database cheese"
PRELOAD="$(admin_sql -d postgres -c 'show shared_preload_libraries')"
# Main's migrations build bm25 indexes, which pg_search refuses unless preloaded.
case ",${PRELOAD// /}," in
  *,pg_search,*) ;;
  *) fail "shared_preload_libraries is '$PRELOAD'; pg_search must be in it (a server restart)" ;;
esac
ENCODING="$(admin_sql -d postgres -c "select pg_encoding_to_char(encoding) from pg_database where datname = 'cheese'")"
log "superuser ok; shared_preload_libraries=$PRELOAD; encoding=$ENCODING"
case "$ENCODING" in SQL_ASCII | UTF8) ;; *) fail "unexpected encoding $ENCODING" ;; esac

OLD_BACKEND="$(docker ps -q --filter label=com.docker.compose.project=cheese \
  --filter label=com.docker.compose.service=backend)"
log "0.16.x backend container: ${OLD_BACKEND:-none running}"

if [ "$DRY_RUN" = 1 ]; then
  log "dry run: every check passed; nothing was changed"
  exit 0
fi

STOPPED=0
on_exit() {
  local rc=$?
  [ "$rc" = 0 ] && return
  if [ "$STOPPED" = 1 ] && [ "$(version 2>/dev/null)" = "$OLD_HEAD" ]; then
    log "the database is still on 0.16.x; starting its backend again"
    docker start "$OLD_BACKEND" >/dev/null || log "could not start $OLD_BACKEND; the site is down until it runs"
  elif [ "$STOPPED" = 1 ]; then
    log "the bridge committed; 0.16.x cannot run on this database, so its backend stays stopped"
  fi
  log "FAILED (exit $rc); log: $LOG"
}
trap on_exit EXIT

if [ -n "$OLD_BACKEND" ]; then
  log "stopping the 0.16.x backend"
  docker stop --time 30 "$OLD_BACKEND" >/dev/null
  STOPPED=1
fi

log "backup"
bash "$HERE/db-backup.sh"
# db-backup.sh names its dump cheese-<its start time>.dump, and a glob sorts.
dumps=("$BACKUP_DIR"/cheese-*.dump)
BACKUP="${dumps[-1]}"
[[ "${BACKUP##*/}" > "cheese-$TS" ]] || fail "db-backup.sh left no dump from this run"
log "backup: $BACKUP"

if [ "$ENCODING" = SQL_ASCII ]; then
  log "rebuilding as UTF8"
  admin env WORK="$BACKUP_DIR/utf8-rebuild-$TS" bash "$HERE/cutover-sqlascii-to-utf8.sh"
else
  log "already UTF8 (an earlier run rebuilt it); no rebuild"
fi

ARCHIVE="$BACKUP_DIR/ruc-016-archive-$TS.dump"
log "archiving what main drops -> $ARCHIVE"
# shellcheck disable=SC2046,SC2086 # the table list splits on purpose
pg_dump -d "$DBURL" -Fc --no-owner --strict-names $(printf -- '-t public.%s ' $ARCHIVE_TABLES) -f "$ARCHIVE"
listing="$(pg_restore --list "$ARCHIVE")"
for t in $ARCHIVE_TABLES; do
  grep -q " TABLE DATA public $t " <<<"$listing" || fail "archive has no data section for $t"
done
log "archive verified: $(wc -w <<<"$ARCHIVE_TABLES") tables, $(du -h "$ARCHIVE" | cut -f1)"
# Same off-site leg as db-backup.sh, and like it a failure there only warns:
# the full backup and cheese_sqlascii_old hold these rows too.
R2_ENV="${CHEESE_R2_ENV:-/home/nictheboy/ops/r2.env}"
R2_UPLOAD="${CHEESE_R2_UPLOAD:-/home/nictheboy/ops/r2-upload.py}"
VENV_PY="${CHEESE_VENV_PY:-/home/nictheboy/cheese-backend-py/backend/.venv/bin/python}"
if [ -f "$R2_ENV" ] && [ -f "$R2_UPLOAD" ] && [ -x "$VENV_PY" ]; then
  # shellcheck disable=SC1090 # a box-local file, not in the repo
  if ( set -a; . "$R2_ENV"; set +a; "$VENV_PY" "$R2_UPLOAD" "$ARCHIVE" ); then
    log "archive uploaded to R2"
  else
    log "WARN: archive R2 upload failed (local copy kept)"
  fi
else
  log "WARN: R2 not configured here; the archive is local only"
fi

log "bridge"
admin psql -X -v ON_ERROR_STOP=1 -1 -d cheese -f "$HERE/cutover-ruc-016-to-main.sql"
[ "$(version)" = "$BRIDGE_REV" ] || fail "bridge ran but the database reads $(version)"
log "database is at $BRIDGE_REV; deploy-docker.sh's upgrade takes it from here"

if [ -n "$OLD_BACKEND" ]; then
  docker rm "$OLD_BACKEND" >/dev/null
  log "removed the 0.16.x backend container"
fi
log "cutover done"
