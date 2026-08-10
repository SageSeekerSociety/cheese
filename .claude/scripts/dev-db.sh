#!/usr/bin/env bash
# Bring up the services the backend test suite needs — Postgres and Redis —
# WITHOUT docker, so `pytest` actually runs inside an agent sandbox.
#
#   eval "$(bash .claude/scripts/dev-db.sh start)"   # start + export TEST_PG_BASE/REDIS_URL
#   cd backend && uv run pytest                      # the suite is now live
#   bash .claude/scripts/dev-db.sh stop --purge       # stop and leave no residue
#
# Only stdout carries `export ...` lines (that is what makes `eval` safe); every
# progress/diagnostic message goes to stderr.
#
# Binaries come from two prebuilt wheels fetched by `uv run --no-project` on a
# pinned Python 3.12: `pgserver` (relocatable PostgreSQL 16) and `redislite`
# (bundled redis-server 6.2). They are deliberately NOT backend dependencies —
# see the note at resolve_bins() below.
set -euo pipefail

DATA_DIR="${CHEESEX_DEV_DB_DIR:-${TMPDIR:-/tmp}/cheesex-dev-db}"
PG_PORT="${CHEESEX_DEV_PG_PORT:-5433}"
REDIS_PORT="${CHEESEX_DEV_REDIS_PORT:-6379}"

# Matches backend/tests/conftest.py's TEST_PG_BASE default: the role and password
# are baked into that default, so initdb has to create exactly this superuser.
PG_USER=cheesex
PG_PASSWORD=cheesex

PGDATA="$DATA_DIR/pg"
REDIS_DIR="$DATA_DIR/redis"
PG_LOG="$DATA_DIR/pg.log"
REDIS_LOG="$DATA_DIR/redis.log"
REDIS_PID="$DATA_DIR/redis.pid"
BIN_CACHE="$DATA_DIR/bins.env"

log() { printf '%s\n' "$*" >&2; }
die() { printf 'dev-db: %s\n' "$*" >&2; exit 1; }

# `pgserver` publishes wheels for cp39–cp312 only, and this project is
# requires-python >=3.13 — so it CANNOT go into backend's dependency groups
# without breaking resolution (`uv` errors: "no wheels with a matching Python ABI
# tag"). Instead both wheels are version-pinned here and fetched onto their own
# throwaway 3.12 interpreter. That interpreter runs nothing but the servers; the
# test suite still runs on the project's 3.13. uv caches wheels and interpreter,
# so only the first call downloads, and the resolved paths are then cached in
# $BIN_CACHE so repeat starts skip uv entirely.
resolve_bins() {
    if [ -f "$BIN_CACHE" ]; then
        # shellcheck disable=SC1090
        . "$BIN_CACHE"
        if [ -x "${PG_BIN:-}/pg_ctl" ] && [ -x "${REDIS_BIN:-}/redis-server" ]; then
            return
        fi
        log "cached binary paths went stale (uv cache pruned?) — re-resolving"
    fi

    log "resolving server binaries via uv (first run downloads ~50MB, then cached)"
    local out
    out="$(uv run --no-project --python 3.12 \
        --with 'pgserver==0.1.4' --with 'redislite==6.2.912183' \
        python -c '
import pathlib, pgserver, redislite
print("PG_BIN=" + str(pathlib.Path(pgserver.__file__).parent / "pginstall" / "bin"))
print("REDIS_BIN=" + str(pathlib.Path(redislite.__file__).parent / "bin"))
')" || die "could not resolve server binaries (is uv installed and the network up?)"

    mkdir -p "$DATA_DIR"
    printf '%s\n' "$out" > "$BIN_CACHE"
    # shellcheck disable=SC1090
    . "$BIN_CACHE"
    [ -x "$PG_BIN/pg_ctl" ] || die "pg_ctl not executable at $PG_BIN"
    [ -x "$REDIS_BIN/redis-server" ] || die "redis-server not executable at $REDIS_BIN"
}

pg_running() { [ -d "$PGDATA" ] && "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; }
redis_running() { "$REDIS_BIN/redis-cli" -h 127.0.0.1 -p "$REDIS_PORT" ping >/dev/null 2>&1; }

start_pg() {
    if pg_running; then
        log "postgres already running on port $PG_PORT"
        return
    fi
    if [ ! -s "$PGDATA/PG_VERSION" ]; then
        log "initdb → $PGDATA (superuser: $PG_USER)"
        rm -rf "$PGDATA"
        mkdir -p "$PGDATA"
        # trust auth: pg_hba lets 127.0.0.1 in without a password, so the password
        # in TEST_PG_BASE is accepted-and-ignored. Sandbox-local socket, no exposure.
        "$PG_BIN/initdb" -D "$PGDATA" -U "$PG_USER" --auth=trust \
            --encoding=UTF8 --locale=C >>"$PG_LOG" 2>&1 \
            || { log "--- initdb log ---"; cat "$PG_LOG" >&2; die "initdb failed"; }
    fi
    log "starting postgres on 127.0.0.1:$PG_PORT"
    # -k keeps the unix socket inside PGDATA so concurrent clusters never collide
    # in /tmp, and so --purge really removes everything.
    "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$PG_LOG" -w -t 60 \
        -o "-p $PG_PORT -h 127.0.0.1 -k $PGDATA" start >/dev/null 2>&1 \
        || { log "--- postgres log ---"; tail -30 "$PG_LOG" >&2; die "postgres failed to start"; }
    "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -t 30 >/dev/null 2>&1 \
        || die "postgres started but never became ready"
}

start_redis() {
    if redis_running; then
        log "redis already running on port $REDIS_PORT"
        return
    fi
    mkdir -p "$REDIS_DIR"
    log "starting redis on 127.0.0.1:$REDIS_PORT"
    # --save '' : test state is disposable, so skip RDB snapshots entirely.
    "$REDIS_BIN/redis-server" \
        --bind 127.0.0.1 --port "$REDIS_PORT" \
        --dir "$REDIS_DIR" --pidfile "$REDIS_PID" --logfile "$REDIS_LOG" \
        --daemonize yes --save '' --appendonly no \
        || die "redis-server failed to start"
    local i
    for i in $(seq 1 30); do
        redis_running && break
        sleep 0.2
        [ "$i" = 30 ] && { log "--- redis log ---"; tail -20 "$REDIS_LOG" >&2; die "redis never became ready"; }
    done
}

print_env() {
    echo "export TEST_PG_BASE=postgresql+asyncpg://$PG_USER:$PG_PASSWORD@127.0.0.1:$PG_PORT"
    echo "export REDIS_URL=redis://127.0.0.1:$REDIS_PORT/0"
}

cmd_start() {
    resolve_bins
    mkdir -p "$DATA_DIR"
    start_pg
    start_redis
    log ""
    log "ready. Run the suite with:"
    log "    eval \"\$(bash .claude/scripts/dev-db.sh start)\" && cd backend && uv run pytest"
    print_env
}

cmd_stop() {
    local purge=0
    [ "${1:-}" = "--purge" ] && purge=1
    resolve_bins
    if pg_running; then
        log "stopping postgres"
        "$PG_BIN/pg_ctl" -D "$PGDATA" -m fast -w -t 60 stop >/dev/null 2>&1 \
            || log "pg_ctl stop reported an error — check $PG_LOG"
    else
        log "postgres not running"
    fi
    if redis_running; then
        log "stopping redis"
        "$REDIS_BIN/redis-cli" -h 127.0.0.1 -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 || true
    else
        log "redis not running"
    fi
    if [ "$purge" = 1 ]; then
        log "purging $DATA_DIR"
        rm -rf "$DATA_DIR"
    else
        log "data kept in $DATA_DIR (re-running start is fast; add --purge to delete)"
    fi
}

cmd_status() {
    resolve_bins
    if pg_running; then log "postgres: RUNNING on 127.0.0.1:$PG_PORT ($PGDATA)"; else log "postgres: stopped"; fi
    if redis_running; then log "redis:    RUNNING on 127.0.0.1:$REDIS_PORT"; else log "redis:    stopped"; fi
}

case "${1:-start}" in
    start)  cmd_start ;;
    stop)   cmd_stop "${2:-}" ;;
    status) cmd_status ;;
    env)    print_env ;;
    *)
        cat >&2 <<EOF
usage: bash .claude/scripts/dev-db.sh <start|stop [--purge]|status|env>

  start           start postgres + redis, print export lines on stdout
  stop            stop both; --purge also deletes $DATA_DIR
  status          report whether each is running
  env             print the export lines without starting anything

env overrides: CHEESEX_DEV_DB_DIR (default \${TMPDIR:-/tmp}/cheesex-dev-db),
               CHEESEX_DEV_PG_PORT (5433), CHEESEX_DEV_REDIS_PORT (6379)
EOF
        exit 2 ;;
esac
