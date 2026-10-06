#!/usr/bin/env bash
# What dev-db.sh must hold to when its Postgres port is already taken:
#   - a server of ours started under other pins (an older PostgreSQL major in
#     its old directory, or the current directory under older pins) is replaced
#     when it has no clients, and refused with the count when it has some;
#   - a server of ours under the current pins is reused, not restarted;
#   - a server this script did not start is refused and left alone;
#   - `stop --purge` stops an older server of ours before deleting its data;
#   - a Redis of ours that is not the pinned Valkey is replaced when it has no
#     other clients, and refused with the count when it has some;
#   - an install that lost files is refused by name, and nothing is started,
#     while `stop` still stops the server that was running from it.
# Every binary it runs is a stand-in on PATH; the "postgres" is a python3
# listener that writes a postmaster.pid, so this needs only bash and python3.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
dev_db="$script_dir/dev-db.sh"
sandbox="$(mktemp -d "${TMPDIR:-/tmp}/dev-db-test.XXXXXX")"
stub="$sandbox/stub"
state="$sandbox/state"
mkdir -p "$stub" "$state"

cleanup() {
    local pid
    while read -r pid; do kill -9 "$pid" 2>/dev/null || true; done <"$state/servers" 2>/dev/null || true
    rm -rf "$sandbox"
}
trap cleanup EXIT

# The pins the script expects, read from the script itself. The installed
# servers are the stand-ins below, linked into a server home of their own.
eval "$(grep -E '^(PG_RELEASE|VALKEY_VERSION|PG_SEARCH_VERSION)=' "$dev_db")"
servers="$sandbox/servers"
pins="postgresql-$PG_RELEASE pg_search==$PG_SEARCH_VERSION $servers/postgresql-$PG_RELEASE"
mkdir -p "$servers/postgresql-$PG_RELEASE" "$servers/valkey-$VALKEY_VERSION"
ln -s "$stub" "$servers/postgresql-$PG_RELEASE/bin"
ln -s "$stub" "$servers/valkey-$VALKEY_VERSION/bin"
# The manifest dev-db.sh checks an install against: the files it was unpacked with.
printf '%s\n' bin/postgres bin/pg_ctl bin/initdb bin/psql bin/pg_isready bin/pg_config \
    bin/lib/plpgsql.so bin/lib/plpgsql.dylib >"$servers/postgresql-$PG_RELEASE/.dev-db-files"
printf '%s\n' bin/valkey-server bin/valkey-cli >"$servers/valkey-$VALKEY_VERSION/.dev-db-files"

# --- stand-ins ---------------------------------------------------------------
cat >"$stub/postgres" <<'PY'
#!/usr/bin/env python3
# A "postmaster": holds the port, writes postmaster.pid, removes it on SIGINT.
import os, signal, socket, sys, time
port, datadir = int(sys.argv[1]), sys.argv[2]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port))
s.listen(64)
pidfile = os.path.join(datadir, "postmaster.pid")
if os.path.isdir(datadir):
    with open(pidfile, "w") as f:
        f.write(f"{os.getpid()}\n{datadir}\n{int(time.time())}\n{port}\n")
def bye(*_):
    try:
        os.unlink(pidfile)
    except OSError:
        pass
    sys.exit(0)
signal.signal(signal.SIGINT, bye)
signal.signal(signal.SIGTERM, bye)
while True:
    time.sleep(1)
PY

cat >"$stub/pg_ctl" <<'SH'
#!/usr/bin/env bash
dir="" opts="" action=""
while [ $# -gt 0 ]; do
    case "$1" in
        -D) dir="$2"; shift 2 ;;
        -o) opts="$2"; shift 2 ;;
        -l|-t|-m) shift 2 ;;
        -w) shift ;;
        *) action="$1"; shift ;;
    esac
done
alive() { [ -s "$dir/postmaster.pid" ] && kill -0 "$(head -1 "$dir/postmaster.pid")" 2>/dev/null; }
case "$action" in
    status) alive ;;
    start)
        port="$(printf '%s\n' "$opts" | sed -E 's/.*-p ([0-9]+).*/\1/')"
        echo "start $dir" >>"$STATE/calls"
        python3 "$STUB/postgres" "$port" "$dir" </dev/null >/dev/null 2>&1 &
        echo $! >>"$STATE/servers"
        for _ in $(seq 1 50); do alive && exit 0; sleep 0.1; done
        exit 1 ;;
    stop)
        alive || exit 0
        pid="$(head -1 "$dir/postmaster.pid")"; kill -INT "$pid"
        for _ in $(seq 1 50); do kill -0 "$pid" 2>/dev/null || exit 0; sleep 0.1; done
        exit 1 ;;
esac
SH

cat >"$stub/initdb" <<'SH'
#!/usr/bin/env bash
while [ $# -gt 0 ]; do [ "$1" = -D ] && dir="$2"; shift; done
mkdir -p "$dir" && echo 17 >"$dir/PG_VERSION"
SH

printf '#!/usr/bin/env bash\nexit 0\n' >"$stub/pg_isready"

# psql answers the client count from $STATE/clients, or fails if asked to.
cat >"$stub/psql" <<'SH'
#!/usr/bin/env bash
[ -e "$STATE/psql_fails" ] && exit 2
cat "$STATE/clients" 2>/dev/null || echo 0
SH

cat >"$stub/pg_config" <<'SH'
#!/usr/bin/env bash
case "$1" in --pkglibdir) echo "$STUB/lib" ;; --sharedir) echo "$STUB/share" ;; esac
SH
mkdir -p "$stub/lib" "$stub/share/extension"
for library in plpgsql pg_trgm pg_search; do touch "$stub/lib/$library.so" "$stub/lib/$library.dylib"; done
echo "default_version = '$PG_SEARCH_VERSION'" >"$stub/share/extension/pg_search.control"

# Redis: a file per port records the --dir it was started with, and one beside
# it the INFO server lines it answers. $STATE/redis-clients holds how many
# clients INFO reports, the one asking included.
cat >"$stub/valkey-server" <<SH
#!/usr/bin/env bash
while [ \$# -gt 0 ]; do
    case "\$1" in --port) port="\$2"; shift 2 ;; --dir) dir="\$2"; shift 2 ;; *) shift ;; esac
done
(cd "\$dir" && pwd -P) >"\$STATE/redis-\$port"
printf 'redis_version:7.2.4\nvalkey_version:%s\n' "$VALKEY_VERSION" >"\$STATE/redis-\$port.info"
SH
cat >"$stub/valkey-cli" <<'SH'
#!/usr/bin/env bash
port=""; while [ "${1:-}" = -h ] || [ "${1:-}" = -p ]; do [ "$1" = -p ] && port="$2"; shift 2; done
f="$STATE/redis-$port"
case "$1 ${2:-}" in
    "ping "*) [ -e "$f" ] ;;
    "config "*) [ -e "$f" ] && { echo dir; cat "$f"; } ;;
    "info server") [ -e "$f" ] && cat "$f.info" ;;
    "info clients") [ -e "$f" ] && echo "connected_clients:$(cat "$STATE/redis-clients" 2>/dev/null || echo 1)" ;;
    "shutdown "*) rm -f "$f" "$f.info" ;;
esac
SH

# uv only answers the harness pins and where the pi archive is cached; the
# harness builds, pi included, are prebuilt below.
tools="$sandbox/tools"
pi_archive="$tools/pi-dist/pi-test.tar.gz"
cat >"$stub/uv" <<SH
#!/usr/bin/env bash
case "\$*" in
    *pi-dist*) echo "$pi_archive" ;;
    *) echo 2.1.282 0.154.0 ;;
esac
SH
harness="$tools/harness/claude-code-2.1.282-codex-0.154.0/node_modules/.bin"
pi_dir="${pi_archive%.tar.gz}/pi"
mkdir -p "$harness" "$pi_dir"
printf '#!/usr/bin/env bash\necho "2.1.282 (Claude Code)"\n' >"$harness/claude"
printf '#!/usr/bin/env bash\necho 0.154.0\n' >"$harness/codex"
printf '#!/usr/bin/env bash\necho 0.85.1\n' >"$pi_dir/pi"
chmod +x "$stub"/* "$harness"/* "$pi_dir/pi"

# --- helpers -----------------------------------------------------------------
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

new_data_dir() {
    local d="$sandbox/$1"
    mkdir -p "$d"
    printf '%s' "$d"
}

# Start a stand-in postgres in $1 on $2, as some earlier run would have.
run_server() {
    mkdir -p "$1"
    python3 "$stub/postgres" "$2" "$1" </dev/null >/dev/null 2>&1 &
    echo $! >>"$state/servers"
    for _ in $(seq 1 50); do [ -s "$1/postmaster.pid" ] && break; sleep 0.1; done
}
server_pid() { head -1 "$1/postmaster.pid"; }

dev_db() {  # dev_db <data dir> <port> <args...>; output in $sandbox/{out,err}, status in $status
    local d="$1" port="$2"; shift 2
    set +e
    PATH="$stub:$PATH" STUB="$stub" STATE="$state" \
        CHEESEX_DEV_DB_DIR="$d" CHEESEX_DEV_PG_PORT="$port" \
        CHEESEX_DEV_REDIS_PORT="${redis_port:-$(free_port)}" CHEESEX_DEV_TOOL_CACHE="$tools" \
        CHEESEX_DEV_SERVER_HOME="$servers" \
        bash "$dev_db" "$@" >"$sandbox/out" 2>"$sandbox/err"
    status=$?
    set -e
}

failures=0
check() {  # check <case> <description> <command...>
    local name="$1" what="$2"; shift 2
    if "$@"; then
        echo "ok   $name: $what"
    else
        echo "FAIL $name: $what"
        echo "     stderr: $(tail -5 "$sandbox/err" | tr '\n' ' ')"
        failures=$((failures + 1))
    fi
}
alive() { kill -0 "$1" 2>/dev/null; }
dead() { ! kill -0 "$1" 2>/dev/null; }
stdout_is_only_exports() { ! grep -qv '^export ' "$sandbox/out"; }

# --- cases -------------------------------------------------------------------
# 1. An older major of ours, idle: replaced, and its directory removed.
d="$(new_data_dir older-idle)"; port="$(free_port)"; echo 0 >"$state/clients"
run_server "$d/pg" "$port"; old="$(server_pid "$d/pg")"
dev_db "$d" "$port" start
check older-idle "start succeeds" [ "$status" = 0 ]
check older-idle "old server stopped" dead "$old"
check older-idle "old cluster dir removed" [ ! -e "$d/pg" ]
check older-idle "new server records the current pins" [ "$(cat "$d/pg17/dev-db.pins" 2>/dev/null)" = "$pins" ]
check older-idle "new server is up" alive "$(server_pid "$d/pg17" 2>/dev/null || echo 0)"
check older-idle "stdout carries only export lines" stdout_is_only_exports

# 2. An older major of ours with clients: refused, left running, count named.
d="$(new_data_dir older-busy)"; port="$(free_port)"; echo 2 >"$state/clients"
run_server "$d/pg" "$port"; old="$(server_pid "$d/pg")"
dev_db "$d" "$port" start
check older-busy "start refuses" [ "$status" != 0 ]
check older-busy "the refusal names the clients" grep -q "2 client connection" "$sandbox/err"
check older-busy "old server left running" alive "$old"
check older-busy "no new server started" [ ! -e "$d/pg17/postmaster.pid" ]
echo 0 >"$state/clients"

# 3. Ours, but its clients cannot be counted: refused, left running.
d="$(new_data_dir older-unknown)"; port="$(free_port)"; touch "$state/psql_fails"
run_server "$d/pg" "$port"; old="$(server_pid "$d/pg")"
dev_db "$d" "$port" start
check older-unknown "start refuses" [ "$status" != 0 ]
check older-unknown "old server left running" alive "$old"
rm -f "$state/psql_fails"

# 4. A server this script did not start: refused and left alone, idle or not.
d="$(new_data_dir foreign)"; port="$(free_port)"
run_server "$sandbox/elsewhere" "$port"; foreign="$(server_pid "$sandbox/elsewhere")"
dev_db "$d" "$port" start
check foreign "start refuses" [ "$status" != 0 ]
check foreign "the refusal says it is not ours" grep -q "did not start" "$sandbox/err"
check foreign "foreign server left running" alive "$foreign"

# 5. Ours under the current pins: reused, not restarted.
d="$(new_data_dir current)"; port="$(free_port)"; : >"$state/calls"
dev_db "$d" "$port" start
first="$(server_pid "$d/pg17")"
dev_db "$d" "$port" start
check current "second start succeeds" [ "$status" = 0 ]
check current "server reused" [ "$(server_pid "$d/pg17")" = "$first" ]
check current "postgres started only once" [ "$(grep -c '^start ' "$state/calls")" = 1 ]

# 6. Ours in the current directory under older pins (e.g. a pg_search bump):
#    restarted in place, data kept.
echo "postgresql-binaries==17.0.0 pg_search==0.1.0" >"$d/pg17/dev-db.pins"
touch "$d/pg17/kept-data"
dev_db "$d" "$port" start
check same-major "start succeeds" [ "$status" = 0 ]
check same-major "old server stopped" dead "$first"
check same-major "restarted on the current pins" [ "$(cat "$d/pg17/dev-db.pins")" = "$pins" ]
check same-major "data dir kept" [ -e "$d/pg17/kept-data" ]

# 7. stop --purge stops an older server of ours before deleting its data dir.
d="$(new_data_dir purge)"; port="$(free_port)"
run_server "$d/pg" "$port"; old="$(server_pid "$d/pg")"
dev_db "$d" "$port" stop --purge
check purge "stop succeeds" [ "$status" = 0 ]
check purge "older server stopped" dead "$old"
check purge "data dir removed" [ ! -e "$d" ]

# 8. A Redis of ours that is not the pinned Valkey: replaced when only the
#    asking client is connected, refused with the count otherwise.
d="$(new_data_dir old-redis)"; port="$(free_port)"; redis_port="$(free_port)"
mkdir -p "$d/redis"
(cd "$d/redis" && pwd -P) >"$state/redis-$redis_port"
echo redis_version:6.2.12 >"$state/redis-$redis_port.info"
echo 3 >"$state/redis-clients"
dev_db "$d" "$port" start
check old-redis-busy "start refuses" [ "$status" != 0 ]
check old-redis-busy "the refusal names the clients" grep -q "2 client connection" "$sandbox/err"
check old-redis-busy "old redis left running" grep -q 6.2.12 "$state/redis-$redis_port.info"
echo 1 >"$state/redis-clients"
dev_db "$d" "$port" start
check old-redis-idle "start succeeds" [ "$status" = 0 ]
check old-redis-idle "now runs the pinned Valkey" grep -q "valkey_version:$VALKEY_VERSION" "$state/redis-$redis_port.info"
dev_db "$d" "$port" stop
unset redis_port
rm -f "$state/redis-clients"

# 9. An install that lost files under a running server: start refuses by name
#    and starts nothing; stop still stops the server.
d="$(new_data_dir damaged)"; port="$(free_port)"
dev_db "$d" "$port" start
running="$(server_pid "$d/pg17")"
mv "$stub/lib/plpgsql.so" "$stub/lib/plpgsql.so.away"; mv "$stub/lib/plpgsql.dylib" "$stub/lib/plpgsql.dylib.away"
: >"$state/calls"
dev_db "$d" "$port" start
check damaged "start refuses" [ "$status" != 0 ]
check damaged "the refusal names the install" grep -q "postgresql-$PG_RELEASE is missing files" "$sandbox/err"
check damaged "no server started" [ ! -s "$state/calls" ]
dev_db "$d" "$port" stop
check damaged "stop succeeds" [ "$status" = 0 ]
check damaged "the running server is stopped" dead "$running"
mv "$stub/lib/plpgsql.so.away" "$stub/lib/plpgsql.so"; mv "$stub/lib/plpgsql.dylib.away" "$stub/lib/plpgsql.dylib"

if [ "$failures" != 0 ]; then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all dev-db.sh checks passed"
