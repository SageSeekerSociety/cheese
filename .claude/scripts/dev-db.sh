#!/usr/bin/env bash
# Bring up what the backend test suite needs from outside the repo — Postgres,
# Redis, and the pinned Claude Code / Codex builds — WITHOUT docker, so
# `pytest` actually runs inside an agent sandbox. CI gets the same three from
# service containers and its "Install pinned harness binaries" step.
#
#   eval "$(bash .claude/scripts/dev-db.sh start)"   # start + export the variables below
#   cd backend && uv run pytest                      # the suite is now live
#   bash .claude/scripts/dev-db.sh stop --purge       # stop and leave no residue
#
# Only stdout carries `export ...` lines (that is what makes `eval` safe); every
# progress/diagnostic message goes to stderr.
#
# The servers are pinned releases installed once into $SERVER_HOME: PostgreSQL 17
# with contrib from the theseus-rs/postgresql-binaries release, ParadeDB's
# pg_search added to it (see install_pg_search), and Valkey, the server CI runs,
# built from its source release (see install_valkey).
# The harness builds are npm packages; they land in $TOOL_CACHE, outside
# $DATA_DIR, so `stop --purge` does not throw away the download.
set -euo pipefail

# Neither the servers nor their data live in a cache or temp directory. A running
# postgres loads plpgsql and pg_search from its $libdir in every new connection
# that uses them, so a `uv cache prune` or disk cleanup that deletes them leaves
# a server that is up but fails those queries; and macOS's
# dirhelper deletes files under $TMPDIR that are older than three days, every
# night at 03:35, data files of a running cluster included.
DATA_DIR="${CHEESEX_DEV_DB_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/cheesex-dev-db}"
SERVER_HOME="${CHEESEX_DEV_SERVER_HOME:-${XDG_DATA_HOME:-$HOME/.local/share}/cheesex-dev-servers}"
PG_PORT="${CHEESEX_DEV_PG_PORT:-5433}"
REDIS_PORT="${CHEESEX_DEV_REDIS_PORT:-6379}"
TOOL_CACHE="${CHEESEX_DEV_TOOL_CACHE:-${XDG_CACHE_HOME:-$HOME/.cache}/cheesex-dev-db}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

# This server's own superuser — it exists only inside this throwaway cluster and
# is handed to the suite through the exported TEST_PG_BASE, which is why `eval`
# is not optional in the usage line above. It used to be documented as "matches
# conftest.py's TEST_PG_BASE default"; that default now names the docker-compose
# TEST server (postgres:postgres@:5433) instead, because that is what a developer
# with Docker actually has. Nothing here depends on the two agreeing.
PG_USER=cheesex
PG_PASSWORD=cheesex

# Named for the major: a cluster initdb'd by another major will not start.
PGDATA="$DATA_DIR/pg17"
# Written into the cluster when it starts: the pins of the server running it.
# A server of ours whose record differs predates the current pins; see start_pg.
PG_STAMP=dev-db.pins
REDIS_DIR="$DATA_DIR/redis"
PG_LOG="$DATA_DIR/pg.log"
REDIS_LOG="$DATA_DIR/redis.log"
REDIS_PID="$DATA_DIR/redis.pid"

log() { printf '%s\n' "$*" >&2; }
die() { printf 'dev-db: %s\n' "$*" >&2; exit 1; }

# The Postgres build must ship contrib: the migrations run
# `CREATE EXTENSION pg_trgm`, which a core-only build (such as the `pgserver`
# wheel) cannot satisfy, so `alembic upgrade head` would fail on it. The
# theseus-rs/postgresql-binaries release tarballs are relocatable and carry
# contrib. Keep the major on the one dev and production run (17).
PG_RELEASE=17.11.0
PG_HOME="$SERVER_HOME/postgresql-$PG_RELEASE"
PG_BIN="$PG_HOME/bin"

# Valkey is what CI runs as its Redis service. Valkey publishes no macOS build,
# so its source release is built here, the same way on every host; the build
# needs only make and a C compiler and takes under a minute. Redis 6.2 is not enough: EXPIRE ... NX,
# which session_host/consumptions.py sends, arrived in 7.0.
VALKEY_VERSION=8.0.2
VALKEY_SHA256=e052c45b3cbe512e24fdfdc3fd337f9f5e4b8f8b8713f349ba867b829c8ff11a
VALKEY_HOME="$SERVER_HOME/valkey-$VALKEY_VERSION"
REDIS_BIN="$VALKEY_HOME/bin"

resolve_bins() {
    mkdir -p "$SERVER_HOME"
    pg_installed || install_pg
    pg_search_installed || install_pg_search
    valkey_installed || install_valkey
}

dl_suffix() { if [ "$(uname -s)" = Darwin ]; then echo dylib; else echo so; fi; }

pg_installed() {
    local tool libdir
    for tool in postgres pg_ctl initdb psql pg_isready pg_config; do
        [ -x "$PG_BIN/$tool" ] || return 1
    done
    libdir="$("$PG_BIN/pg_config" --pkglibdir 2>/dev/null)" || return 1
    [ -f "$libdir/plpgsql.$(dl_suffix)" ] && [ -f "$libdir/pg_trgm.$(dl_suffix)" ]
}

valkey_installed() { [ -x "$REDIS_BIN/valkey-server" ] && [ -x "$REDIS_BIN/valkey-cli" ]; }

# Download $1 to $3 unless a file with sha256 $2 is already there. Plain Python,
# so the host needs no curl. The file is written under a temporary name and
# renamed into place, so a concurrent start never reads half a download.
fetch() {
    uv run --no-project --quiet python - "$@" <<'PY' || die "download of $1 failed (see above)"
import hashlib, os, pathlib, sys, time, urllib.request

url, want, dest = sys.argv[1:]
dest = pathlib.Path(dest)


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


if not (dest.is_file() and sha256(dest) == want):
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f"{dest.name}.{os.getpid()}.part")
    for attempt in range(3):
        try:
            urllib.request.urlretrieve(url, part)
            break
        except OSError as e:
            if attempt == 2:
                sys.exit(f"download of {url} failed: {e}")
            time.sleep(2)
    got = sha256(part)
    if got != want:
        part.unlink()
        sys.exit(f"{url} has sha256 {got}, expected {want}")
    os.replace(part, dest)
PY
}

# Move the finished install $1 to $2. $2 appears only once complete, so a
# concurrent start sees all of an install or none of it. An install that won a
# race to $2 is kept; it is just as complete.
place() {
    [ -e "$2" ] || mv "$1" "$2"
    # Lost the race between the test and the mv, which then put $1 inside $2.
    rm -rf "${2:?}/$(basename "$1")"
}

# An install that exists but fails its check had files deleted from under it.
# It is not repaired in place: servers may be running from it, and swapping
# files under them is how they got into this state.
damaged() {
    die "$1 is missing files (something deleted part of it); stop the servers running from it, delete that directory, and start again."
}

install_pg() {
    [ ! -e "$PG_HOME" ] || damaged "the PostgreSQL install in $PG_HOME"
    local target sha256
    case "$(uname -s)/$(uname -m)" in
        Darwin/arm64)
            target=aarch64-apple-darwin
            sha256=fd4b62794b160e26973a768a1eef3248aef9d2ff23ebd6d884a4299485e28e57 ;;
        Linux/x86_64)
            target=x86_64-unknown-linux-gnu
            sha256=b7a1ba6bae6499d8296e3e81b0171eecfd1766ca9aaa0057e41ad3e844e5e2e0 ;;
        Linux/aarch64 | Linux/arm64)
            target=aarch64-unknown-linux-gnu
            sha256=abffda09209280ec1502b73720dc4d254fb7fff9a072e324926c600a5b16c221 ;;
        *)
            die "no PostgreSQL $PG_RELEASE build is set up for $(uname -s)/$(uname -m), only for
       macOS arm64 and Linux x86_64 and aarch64, the platforms pg_search is set up for." ;;
    esac
    local asset="postgresql-$PG_RELEASE-$target.tar.gz" work
    log "installing PostgreSQL $PG_RELEASE into $PG_HOME (first run downloads ~15MB)"
    fetch "https://github.com/theseus-rs/postgresql-binaries/releases/download/$PG_RELEASE/$asset" \
        "$sha256" "$TOOL_CACHE/postgresql/$asset"
    work="$(mktemp -d "$SERVER_HOME/.install.XXXXXX")"
    tar -xzf "$TOOL_CACHE/postgresql/$asset" -C "$work" \
        || { rm -rf "$work"; die "cannot unpack $TOOL_CACHE/postgresql/$asset"; }
    place "$work/postgresql-$PG_RELEASE-$target" "$PG_HOME"
    rm -rf "$work"
    pg_installed || damaged "the PostgreSQL install in $PG_HOME"
}

install_valkey() {
    [ ! -e "$VALKEY_HOME" ] || damaged "the Valkey install in $VALKEY_HOME"
    { command -v make && command -v cc; } >/dev/null 2>&1 \
        || die "building Valkey $VALKEY_VERSION needs make and a C compiler (cc); this host lacks one."
    local archive="$TOOL_CACHE/valkey/valkey-$VALKEY_VERSION.tar.gz" work
    log "building Valkey $VALKEY_VERSION into $VALKEY_HOME (first run only)"
    fetch "https://github.com/valkey-io/valkey/archive/refs/tags/$VALKEY_VERSION.tar.gz" \
        "$VALKEY_SHA256" "$archive"
    work="$(mktemp -d "$SERVER_HOME/.install.XXXXXX")"
    { tar -xzf "$archive" -C "$work" \
        && make -C "$work/valkey-$VALKEY_VERSION" -j4 \
        && make -C "$work/valkey-$VALKEY_VERSION" PREFIX="$work/valkey" install; } >"$work/build.log" 2>&1 \
        || { tail -20 "$work/build.log" >&2; rm -rf "$work"; die "building Valkey $VALKEY_VERSION failed (log tail above)"; }
    place "$work/valkey" "$VALKEY_HOME"
    rm -rf "$work"
    valkey_installed || damaged "the Valkey install in $VALKEY_HOME"
}

# The migrations run `CREATE EXTENSION pg_search` (ParadeDB's BM25 index, which
# dev and production get from the paradedb image). theseus-rs does not build it,
# so it comes from ParadeDB's own release: the Debian bookworm package for
# PostgreSQL 17. Every Linux build ParadeDB publishes needs glibc 2.34, and this
# one links nothing beyond libc, libm and libgcc_s, so it loads into the
# relocatable server on any Linux with that glibc. Only pg_search.so and the
# extension's SQL and control files are taken from the .deb; they go into this
# server's own pkglibdir and sharedir, next to pg_trgm. Keep the version on the
# one dev and production run (docker-compose.yml's paradedb image).
PG_SEARCH_VERSION=0.24.0
PG_SEARCH_GLIBC_MIN=2.34

pg_search_installed() {
    local libdir sharedir library=pg_search.so
    [ "$(uname -s)" != Darwin ] || library=pg_search.dylib
    libdir="$("$PG_BIN/pg_config" --pkglibdir 2>/dev/null)" || return 1
    sharedir="$("$PG_BIN/pg_config" --sharedir 2>/dev/null)" || return 1
    [ -f "$libdir/$library" ] \
        && grep -qx "default_version = '$PG_SEARCH_VERSION'" "$sharedir/extension/pg_search.control" 2>/dev/null
}

install_pg_search() {
    local os arch deb_arch sha256
    os="$(uname -s)"
    arch="$(uname -m)"
    case "$os/$arch" in
        Linux/x86_64)
            deb_arch=amd64
            sha256=6ca0329d67bcea07518a97e2aae77f3a2ecdc9a10e2cf88eddb87c774a804f4a ;;
        Linux/aarch64 | Linux/arm64)
            deb_arch=arm64
            sha256=56d738139ef86bbda90fe451d86181571dbdebddbf10579abb92346a65140c8f ;;
        Darwin/arm64)
            install_pg_search_macos
            return ;;
        *)
            die "no pg_search $PG_SEARCH_VERSION build is set up for $os/$arch, only for Linux x86_64 and
       aarch64 and macOS arm64. Without pg_search the migrations fail, and with them every
       DB-backed test." ;;
    esac
    local glibc
    glibc="$(getconf GNU_LIBC_VERSION 2>/dev/null)" || glibc=""
    glibc="${glibc#glibc }"
    if [ -z "$glibc" ] \
        || [ "$(printf '%s\n%s\n' "$PG_SEARCH_GLIBC_MIN" "$glibc" | sort -V | head -1)" != "$PG_SEARCH_GLIBC_MIN" ]; then
        die "pg_search $PG_SEARCH_VERSION needs glibc $PG_SEARCH_GLIBC_MIN or newer; this system has ${glibc:-no glibc}."
    fi

    local asset="postgresql-17-pg-search_${PG_SEARCH_VERSION}-1PARADEDB-bookworm_${deb_arch}.deb"
    local url="https://github.com/paradedb/paradedb/releases/download/v$PG_SEARCH_VERSION/$asset"
    local libdir sharedir
    libdir="$("$PG_BIN/pg_config" --pkglibdir)" || die "pg_config at $PG_BIN does not run"
    sharedir="$("$PG_BIN/pg_config" --sharedir)" || die "pg_config at $PG_BIN does not run"
    log "installing pg_search $PG_SEARCH_VERSION into $libdir (first run downloads ~70MB, then cached)"
    # The .deb is kept in $TOOL_CACHE, so `stop --purge` keeps it too. Plain
    # Python, so the host needs no ar or xz. Each file is written under a
    # temporary name and renamed into place, so a start running concurrently
    # never loads a half-written library.
    fetch "$url" "$sha256" "$TOOL_CACHE/pg_search/$asset"
    uv run --no-project --quiet python - \
        "$TOOL_CACHE/pg_search/$asset" "$libdir" "$sharedir/extension" <<'PY' \
        || die "installing pg_search $PG_SEARCH_VERSION failed (see above)"
import io, os, pathlib, shutil, sys, tarfile

deb_path, libdir, extdir = sys.argv[1:]
deb = pathlib.Path(deb_path)

# A .deb is an ar archive; the installed files are in its data.tar.* member.
data = None
with open(deb, "rb") as f:
    if f.read(8) != b"!<arch>\n":
        sys.exit(f"{deb} is not a .deb")
    while len(header := f.read(60)) == 60:
        name = header[:16].decode().strip().rstrip("/")
        size = int(header[48:58].decode())
        if name.startswith("data.tar"):
            data = f.read(size)
            break
        f.seek(size + size % 2, 1)
if data is None:
    sys.exit(f"{deb} has no data.tar member")

targets = {
    "usr/lib/postgresql/17/lib/": pathlib.Path(libdir),
    "usr/share/postgresql/17/extension/": pathlib.Path(extdir),
}
placed = set()
with tarfile.open(fileobj=io.BytesIO(data)) as tar:
    for member in tar:
        path = member.name.removeprefix("./")
        for prefix, dest in targets.items():
            name = path.removeprefix(prefix)
            if member.isfile() and name != path and "/" not in name:
                out = dest / name
                tmp = dest / f".{name}.{os.getpid()}"
                with tar.extractfile(member) as src, open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                tmp.chmod(0o755 if name.endswith(".so") else 0o644)
                os.replace(tmp, out)
                placed.add(name)
if not {"pg_search.so", "pg_search.control"} <= placed:
    sys.exit(f"{deb} does not contain pg_search.so and pg_search.control")
PY
    pg_search_installed || die "pg_search $PG_SEARCH_VERSION is still missing from $libdir after install"
}

# macOS arm64 takes ParadeDB's installer package for Homebrew's PostgreSQL 17,
# built on macOS 15 and loading on later releases. Its library links nothing
# beyond the system's own, so, as on Linux, only the library and the extension's
# SQL and control files are taken, into this server's own directories. pkgutil
# ships with macOS.
install_pg_search_macos() {
    local asset="pg_search@17--$PG_SEARCH_VERSION.arm64_sequoia.pkg"
    local sha256=ffc8a840b191cb72367ee5d846ebe0f5d9509eea09518e7734cfabbd71f5e76a
    local url="https://github.com/paradedb/paradedb/releases/download/v$PG_SEARCH_VERSION/$asset"
    local pkg="$TOOL_CACHE/pg_search/$asset" libdir extdir work
    libdir="$("$PG_BIN/pg_config" --pkglibdir)" || die "pg_config at $PG_BIN does not run"
    extdir="$("$PG_BIN/pg_config" --sharedir)/extension" || die "pg_config at $PG_BIN does not run"
    log "installing pg_search $PG_SEARCH_VERSION into $libdir (first run downloads ~80MB, then cached)"
    fetch "$url" "$sha256" "$pkg"
    work="$(mktemp -d)"
    pkgutil --expand-full "$pkg" "$work/pkg" >/dev/null || { rm -rf "$work"; die "cannot expand $pkg"; }
    local payload="$work/pkg/Payload" from to
    # Each file is copied under a temporary name and renamed into place, so a
    # start running concurrently never loads a half-written library.
    for from in lib/postgresql/pg_search.dylib \
        share/postgresql@17/extension/pg_search.control \
        "share/postgresql@17/extension/pg_search--$PG_SEARCH_VERSION.sql"; do
        case "$from" in lib/*) to="$libdir" ;; *) to="$extdir" ;; esac
        [ -f "$payload/$from" ] || { rm -rf "$work"; die "$pkg has no $from"; }
        cp "$payload/$from" "$to/.$(basename "$from").$$" \
            && mv "$to/.$(basename "$from").$$" "$to/$(basename "$from")"
    done
    rm -rf "$work"
    pg_search_installed || die "pg_search $PG_SEARCH_VERSION is still missing from $libdir after install"
}

pg_running() { [ -d "$PGDATA" ] && "$PG_BIN/pg_ctl" -D "$PGDATA" status >/dev/null 2>&1; }
# The install path is part of the pins, so a server started from somewhere else
# (an older version of this script ran it out of the uv cache) is replaced too.
pg_pins() { printf '%s' "postgresql-$PG_RELEASE pg_search==$PG_SEARCH_VERSION $PG_HOME"; }
redis_running() { "$REDIS_BIN/valkey-cli" -h 127.0.0.1 -p "$REDIS_PORT" ping >/dev/null 2>&1; }

# --- identity guards -------------------------------------------------------
# A port that answers is NOT proof the server behind it is ours. Silently reusing
# a stranger's Postgres or Redis does not crash anything — it yields a pile of
# semantically unrelated test failures, and the reader burns real time auditing
# their own code before suspecting the database. So: prove identity before
# reusing, and refuse loudly when it cannot be proven.

# bash's own TCP redirection — no `nc`/`ss` in the sandbox. If the shell
# was built without it the probe just fails, degrading to the old behaviour.
port_in_use() { (exec 3<>"/dev/tcp/127.0.0.1/$1") >/dev/null 2>&1; }

# postmaster.pid: line 1 = pid, line 2 = data dir, line 4 = port.
pg_pidfile_port() { [ -s "$PGDATA/postmaster.pid" ] && awk 'NR==4' "$PGDATA/postmaster.pid"; }

# `dir` is the redis server's own working directory; ours is always $REDIS_DIR.
# A server that refuses CONFIG GET (renamed or disabled) reports nothing and so
# fails this check — which is the right answer, because ours never refuses.
redis_reported_dir() {
    "$REDIS_BIN/valkey-cli" -h 127.0.0.1 -p "$REDIS_PORT" config get dir 2>/dev/null | tail -1
}
redis_is_ours() {
    local want got
    want="$( (cd "$REDIS_DIR" 2>/dev/null && pwd -P) || printf '%s' "$REDIS_DIR" )"
    got="$(redis_reported_dir)"
    [ -n "$got" ] && [ "$got" = "$want" ]
}

# The cluster directory under $DATA_DIR whose live postmaster serves $PG_PORT,
# whichever version of this script started it (older ones used other directory
# names, e.g. `pg` for PostgreSQL 16). Proof of ownership is the postmaster.pid
# inside our own data dir naming this port, with its pid alive and a postgres
# process; a pid file left behind by a crash can name a pid since reused.
our_cluster_on_port() {
    local pidfile pid
    for pidfile in "$DATA_DIR"/*/postmaster.pid; do
        [ -s "$pidfile" ] || continue
        [ "$(awk 'NR==4' "$pidfile")" = "$PG_PORT" ] || continue
        pid="$(head -1 "$pidfile")"
        kill -0 "$pid" 2>/dev/null || continue
        case "$(ps -o args= -p "$pid" 2>/dev/null)" in *postgres*) ;; *) continue ;; esac
        dirname "$pidfile"
        return
    done
    return 1
}

# Stop our server in $1 so the current pins can take the port: fast shutdown by
# signal, since the pg_ctl of the version that started it may be gone from the
# uv cache. A cluster in another directory belongs to another major and will
# never start again, so its (throwaway) data goes with it.
stop_our_cluster() {
    local dir="$1" pid i
    pid="$(head -1 "$dir/postmaster.pid")"
    kill -INT "$pid" 2>/dev/null || true
    for i in $(seq 1 120); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.5
    done
    kill -0 "$pid" 2>/dev/null && die "postgres (pid $pid, $dir) did not shut down within 60s"
    [ "$dir" = "$PGDATA" ] || rm -rf "$dir"
}

# A server of ours started under other pins (another PostgreSQL or pg_search)
# is replaced, but only while nobody is using it: stopping it would drop some
# other run's connections mid-suite. With clients, refuse and say so.
replace_outdated_pg() {
    local dir="$1" had clients
    had="$(cat "$dir/$PG_STAMP" 2>/dev/null)" || had="no pins recorded"
    clients="$("$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -d postgres -Atc \
        "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'client backend' AND pid <> pg_backend_pid()" \
        2>/dev/null)" \
        || die "the postgres on port $PG_PORT ($dir) was started by this script under other pins ($had),
       but asking it for its clients failed, so it is left running. Stop it by hand
       once it is idle, then start again."
    if [ "$clients" != 0 ]; then
        die "the postgres on port $PG_PORT ($dir) was started by this script under other pins
       ($had; now $(pg_pins)), and it has $clients client connection(s).
       Replacing it would cut them off mid-run, so it is left running. Start again once
       it is idle, or use CHEESEX_DEV_PG_PORT/CHEESEX_DEV_DB_DIR for a server of your own."
    fi
    log "replacing the idle postgres on port $PG_PORT ($dir; $had → $(pg_pins))"
    stop_our_cluster "$dir"
}

start_pg() {
    local ours
    if ours="$(our_cluster_on_port)"; then
        if [ "$ours" = "$PGDATA" ] && [ "$(cat "$PGDATA/$PG_STAMP" 2>/dev/null)" = "$(pg_pins)" ]; then
            log "postgres already running on port $PG_PORT ($PGDATA)"
            return
        fi
        replace_outdated_pg "$ours"
    fi
    if pg_running; then
        local running_port
        running_port="$(pg_pidfile_port)" || running_port=""
        if [ "$running_port" != "$PG_PORT" ]; then
            die "the cluster in $PGDATA is up on port ${running_port:-unknown}, not the $PG_PORT this call asks for.
       Exporting TEST_PG_BASE for $PG_PORT would point the suite at a server that
       is not this one. Re-run with CHEESEX_DEV_PG_PORT=${running_port:-<its port>}, or stop it first."
        fi
        die "the cluster in $PGDATA is up but does not serve port $PG_PORT (see its postmaster.pid)."
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
    #
    # fsync, full_page_writes and synchronous_commit are off because this cluster
    # holds throwaway test data, so crash safety buys nothing, and with them on the
    # suite fails here: it creates thousands of databases, DROP DATABASE waits for
    # a checkpoint, and a checkpoint that must fsync all of them took 200-300s on
    # a Mac. That passes pytest-timeout's 300s, which then ends the xdist worker
    # mid-teardown ("worker crashed" on migration tests that drop their own DB).
    "$PG_BIN/pg_ctl" -D "$PGDATA" -l "$PG_LOG" -w -t 60 \
        -o "-p $PG_PORT -h 127.0.0.1 -k $PGDATA -c fsync=off -c full_page_writes=off -c synchronous_commit=off -c shared_preload_libraries=pg_search" \
        start >/dev/null 2>&1 \
        || { log "--- postgres log ---"; tail -30 "$PG_LOG" >&2; die "postgres failed to start"; }
    "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" -U "$PG_USER" -t 30 >/dev/null 2>&1 \
        || die "postgres started but never became ready"
    pg_pins >"$PGDATA/$PG_STAMP"
}

# "Valkey 8.0.2", or "Redis 6.2.x" for a server that is not Valkey.
redis_server_version() {
    "$REDIS_BIN/valkey-cli" -h 127.0.0.1 -p "$REDIS_PORT" info server 2>/dev/null | tr -d '\r' \
        | awk -F: '$1 == "valkey_version" { v = "Valkey " $2 } $1 == "redis_version" { r = "Redis " $2 }
                   END { print (v != "" ? v : r) }'
}

# A Redis of ours that is not the pinned Valkey (an older version of this script
# ran redislite's 6.2) is replaced on the same terms as an outdated postgres:
# only while no other client is connected.
replace_outdated_redis() {
    local had clients
    had="$(redis_server_version)"
    clients="$("$REDIS_BIN/valkey-cli" -h 127.0.0.1 -p "$REDIS_PORT" info clients 2>/dev/null \
        | tr -d '\r' | sed -n 's/^connected_clients://p')"
    [ -n "$clients" ] || die "the redis on port $REDIS_PORT ($REDIS_DIR, ${had:-unknown version}) was started by this
       script before it pinned Valkey $VALKEY_VERSION, but asking it for its clients failed,
       so it is left running. Stop it by hand once it is idle, then start again."
    clients=$((clients - 1)) # the connection asking
    if [ "$clients" != 0 ]; then
        die "the redis on port $REDIS_PORT ($REDIS_DIR) is ${had:-of unknown version}, not Valkey $VALKEY_VERSION,
       and it has $clients client connection(s). Replacing it would cut them off mid-run,
       so it is left running. Start again once it is idle, or use
       CHEESEX_DEV_REDIS_PORT/CHEESEX_DEV_DB_DIR for a server of your own."
    fi
    log "replacing the idle redis on port $REDIS_PORT ($had → Valkey $VALKEY_VERSION)"
    "$REDIS_BIN/valkey-cli" -h 127.0.0.1 -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 || true
    local i
    for i in $(seq 1 50); do
        redis_running || return 0
        sleep 0.2
    done
    die "the redis on port $REDIS_PORT did not shut down within 10s"
}

start_redis() {
    if redis_running && redis_is_ours && [ "$(redis_server_version)" != "Valkey $VALKEY_VERSION" ]; then
        replace_outdated_redis
    fi
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
    "$REDIS_BIN/valkey-server" \
        --bind 127.0.0.1 --port "$REDIS_PORT" \
        --dir "$REDIS_DIR" --pidfile "$REDIS_PID" --logfile "$REDIS_LOG" \
        --daemonize yes --save '' --appendonly no \
        || die "valkey-server failed to start"
    local i
    for i in $(seq 1 30); do
        redis_running && break
        sleep 0.2
        [ "$i" = 30 ] && { log "--- redis log ---"; tail -20 "$REDIS_LOG" >&2; die "redis never became ready"; }
    done
    # --daemonize yes makes valkey-server exit 0 before it binds, so a bind failure
    # leaves us pinging whoever DID win the port. Confirm the answer is ours.
    redis_is_ours || { log "--- redis log ---"; tail -20 "$REDIS_LOG" >&2
        die "port $REDIS_PORT answers, but not from the server we just started — it lost the bind to another Redis."; }
}

# --- pinned harness builds -------------------------------------------------
# tests/pinned_claude.py takes the builds only from CHEESE_TEST_CLAUDE,
# CHEESE_TEST_CODEX and CHEESE_TEST_PI, never from PATH: on a developer machine
# the `claude` there is often a wrapper or another version, which fails the
# remote-execution and runner tests for reasons unrelated to the code. Install
# exactly what CI installs: the versions come from the checked-in declarations,
# so this cannot drift from the code under test.
resolve_harness() {
    local pins claude_v codex_v
    pins="$(cd "$REPO_ROOT/backend" && uv run --quiet python -c '
from app.domain.agent.capability.matrix import written
from app.domain.agent.harness import CLAUDE_CODE, CODEX
pins = {name: d.pinned_version for name, d in written().items()}
print(pins[CLAUDE_CODE], pins[CODEX])
')" || die "could not read the pinned harness versions from backend/"
    read -r claude_v codex_v <<<"$pins"
    resolve_pi
    local dir="$TOOL_CACHE/harness/claude-code-$claude_v-codex-$codex_v"
    HARNESS_BIN="$dir/node_modules/.bin"
    local have
    have="$("$HARNESS_BIN/claude" --version 2>/dev/null)" || have=""
    if [ "${have%% *}" = "$claude_v" ] && [ -x "$HARNESS_BIN/codex" ]; then
        return
    fi
    command -v npm >/dev/null 2>&1 || die "npm is required to install the pinned Claude Code $claude_v"
    log "installing Claude Code $claude_v and Codex $codex_v into $dir"
    mkdir -p "$dir"
    npm install --prefix "$dir" --no-audit --no-fund --silent \
        "@anthropic-ai/claude-code@$claude_v" "@openai/codex@$codex_v" >&2 \
        || die "npm install of the pinned harness builds failed"
    have="$("$HARNESS_BIN/claude" --version 2>&1)" || die "installed claude does not run: $have"
    [ "${have%% *}" = "$claude_v" ] || die "installed claude reports '$have', expected $claude_v"
}

# pi is the vendor's compiled release, not an npm package: fetched and checked
# by the same code the platform serves it with, exactly as test.yml does.
resolve_pi() {
    local os arch
    case "$(uname -s)" in Darwin) os=darwin ;; Linux) os=linux ;; *) die "no pi build for $(uname -s)" ;; esac
    case "$(uname -m)" in x86_64) arch=x64 ;; arm64 | aarch64) arch=arm64 ;; *) die "no pi build for $(uname -m)" ;; esac
    local archive
    archive="$(cd "$REPO_ROOT/backend" && uv run --quiet python - "$TOOL_CACHE/pi-dist" "$os-$arch" <<'PY'
import asyncio, sys
from pathlib import Path
from app.domain.agent.harness import PI
from app.domain.agent.capability.matrix import written
from app.domain.machine import pi_dist
version = written()[PI].pinned_version
print(asyncio.run(pi_dist.ensure_cached(Path(sys.argv[1]), version, sys.argv[2])))
PY
)" || die "could not fetch the pinned pi build"
    local dir="${archive%.tar.gz}"
    PI_BIN="$dir/pi/pi"
    if [ ! -x "$PI_BIN" ]; then
        mkdir -p "$dir"
        tar -xzf "$archive" -C "$dir" || die "could not unpack $archive"
    fi
    "$PI_BIN" --version >/dev/null 2>&1 || die "the pinned pi build at $PI_BIN does not run"
}

print_env() {
    echo "export TEST_PG_BASE=postgresql+asyncpg://$PG_USER:$PG_PASSWORD@127.0.0.1:$PG_PORT"
    echo "export REDIS_URL=redis://127.0.0.1:$REDIS_PORT/0"
    printf 'export CHEESE_TEST_CLAUDE=%q\n' "$HARNESS_BIN/claude"
    printf 'export CHEESE_TEST_CODEX=%q\n' "$HARNESS_BIN/codex"
    printf 'export CHEESE_TEST_PI=%q\n' "$PI_BIN"
    # Same as CI's GITHUB_PATH: code that looks `claude`/`codex` up on PATH, not
    # through CHEESE_TEST_CLAUDE, must find the pinned builds too.
    # shellcheck disable=SC2016 # $PATH is for the eval-ing shell to expand
    printf 'export PATH=%q:"$PATH"\n' "$HARNESS_BIN"
}

cmd_start() {
    resolve_bins
    resolve_harness
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
    # One an older version of this script started, in another directory: without
    # this, --purge would delete its data dir from under a server still running.
    local ours
    if ours="$(our_cluster_on_port)" && [ "$ours" != "$PGDATA" ]; then
        log "stopping the older postgres in $ours"
        stop_our_cluster "$ours"
    fi
    if redis_running; then
        # Same guard as start, for a bigger reason: an unconditional SHUTDOWN here
        # would kill a Redis that belongs to someone else entirely.
        if redis_is_ours; then
            log "stopping redis"
            "$REDIS_BIN/valkey-cli" -h 127.0.0.1 -p "$REDIS_PORT" shutdown nosave >/dev/null 2>&1 || true
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
        [ "$(cat "$PGDATA/$PG_STAMP" 2>/dev/null)" = "$(pg_pins)" ] \
            || log "          NOTE: started under other pins; the next start replaces it once it is idle"
    elif port_in_use "$PG_PORT"; then
        log "postgres: stopped — but port $PG_PORT is occupied by someone else"
    else
        log "postgres: stopped"
    fi
    if redis_running; then
        if redis_is_ours; then
            log "redis:    RUNNING on 127.0.0.1:$REDIS_PORT ($REDIS_DIR, $(redis_server_version))"
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
    env)    resolve_harness; print_env ;;
    *)
        cat >&2 <<EOF
usage: bash .claude/scripts/dev-db.sh <start|stop [--purge]|status|env>

  start           start postgres + redis, install the pinned
                  harness builds, print export lines on stdout
  stop            stop the servers; --purge also deletes $DATA_DIR
                  (the servers in $SERVER_HOME and downloads in
                  $TOOL_CACHE are kept)
  status          report whether each server is running
  env             print the export lines without starting anything

env overrides: CHEESEX_DEV_DB_DIR (default \${XDG_DATA_HOME:-~/.local/share}/cheesex-dev-db),
               CHEESEX_DEV_SERVER_HOME (default \${XDG_DATA_HOME:-~/.local/share}/cheesex-dev-servers),
               CHEESEX_DEV_PG_PORT (5433), CHEESEX_DEV_REDIS_PORT (6379),
               CHEESEX_DEV_TOOL_CACHE (default \${XDG_CACHE_HOME:-~/.cache}/cheesex-dev-db)
EOF
        exit 2 ;;
esac
