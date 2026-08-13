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

# --- identity guards -------------------------------------------------------
# A port that answers is NOT proof the server behind it is ours. Silently reusing
# a stranger's Postgres or Redis does not crash anything — it yields a pile of
# semantically unrelated test failures, and the reader burns real time auditing
# their own code before suspecting the database. So: prove identity before
# reusing, and refuse loudly when it cannot be proven.

# bash's own TCP redirection — no `nc`/`ss`/procps in the sandbox. If the shell
# was built without it the probe just fails, degrading to the old behaviour.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# postmaster.pid: line 1 = pid, line 2 = data dir, line 4 = port.
pg_pidfile_port() { [ -s "$PGDATA/postmaster.pid" ] && awk 'NR==4' "$PGDATA/postmaster.pid"; }

# `dir` is the redis server's own working directory; ours is always $REDIS_DIR.
# A server that refuses CONFIG GET (renamed or disabled) reports nothing and so
# fails this check — which is the right answer, because ours never refuses.
redis_reported_dir() {
    "$REDIS_BIN/redis-cli" -h 127.0.0.1 -p "$REDIS_PORT" config get dir 2>/dev/null | tail -1
}
redis_is_ours() {
    local want got
    want="$( (cd "$REDIS_DIR" 2>/dev/null && pwd -P) || printf '%s' "$REDIS_DIR" )"
    got="$(redis_reported_dir)"
    [ -n "$got" ] && [ "$got" = "$want" ]
}

start_pg() {
    if pg_running; then
        local running_port
        running_port="$(pg_pidfile_port)" || running_port=""
        if [ "$running_port" != "$PG_PORT" ]; then
            die "the cluster in $PGDATA is up on port ${running_port:-unknown}, not the $PG_PORT this call asks for.
       Exporting TEST_PG_BASE for $PG_PORT would point the suite at a server that
       is not this one. Re-run with CHEESEX_DEV_PG_PORT=${running_port:-<its port>}, or stop it first."
        fi
        log "postgres already running on port $PG_PORT ($PGDATA)"
        return
    fi
    if port_in_use "$PG_PORT"; then
        die "port $PG_PORT is already taken by a server this script did not start.
       Reusing it would run the suite against someone else's database — the
       failures would read as application bugs, not as a port collision.
       Free the port, or re-run with CHEESEX_DEV_PG_PORT=<free port>."
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
        if redis_is_ours; then
            log "redis already running on port $REDIS_PORT ($REDIS_DIR)"
            return
        fi
        local foreign_dir
        foreign_dir="$(redis_reported_dir)" || foreign_dir=""
        die "port $REDIS_PORT already answers to a Redis this script did not start (its dir: ${foreign_dir:-unreported}).
       Sharing it would mix this suite's keys with that server's — login
       rate-limiter lockouts and sessions would leak in both directions.
       Re-run with CHEESEX_DEV_REDIS_PORT=<free port>, or stop that server."
    fi
    if port_in_use "$REDIS_PORT"; then
        die "port $REDIS_PORT is taken by something that does not answer PING.
       Re-run with CHEESEX_DEV_REDIS_PORT=<free port>."
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
    # --daemonize yes makes redis-server exit 0 before it binds, so a bind failure
    # leaves us pinging whoever DID win the port. Confirm the answer is ours.
    redis_is_ours || { log "--- redis log ---"; tail -20 "$REDIS_LOG" >&2
        die "port $REDIS_PORT answers, but not from the server we just started — it lost the bind to another Redis."; }
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
        # Same guard as start, for a bigger reason: an unconditional SHUTDOWN here
        # would kill a Redis that belongs to someone else entirely.
        if redis_is_ours; then
            log "stopping redis"
            "$REDIS_BIN/redis-cli" -h 127.0.0.1 -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 || true
        else
            log "redis on port $REDIS_PORT is not ours — leaving it alone"
        fi
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
    if pg_running; then
        local running_port
        running_port="$(pg_pidfile_port)" || running_port=""
        log "postgres: RUNNING on 127.0.0.1:${running_port:-?} ($PGDATA)"
        [ "$running_port" = "$PG_PORT" ] || log "          NOTE: that is not the \$CHEESEX_DEV_PG_PORT ($PG_PORT) this call assumes"
    elif port_in_use "$PG_PORT"; then
        log "postgres: stopped — but port $PG_PORT is occupied by someone else"
    else
        log "postgres: stopped"
    fi
    if redis_running; then
        if redis_is_ours; then
            log "redis:    RUNNING on 127.0.0.1:$REDIS_PORT ($REDIS_DIR)"
        else
            local foreign_dir
            foreign_dir="$(redis_reported_dir)" || foreign_dir=""
            log "redis:    port $REDIS_PORT answers, but it is NOT ours (dir: ${foreign_dir:-unreported})"
        fi
    else
        log "redis:    stopped"
    fi
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
