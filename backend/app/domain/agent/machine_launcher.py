"""设备启动脚本里平台那一半：换哪个 harness 都一样的部分。

A device launcher is one shell script, and two different things are written in
it. One is this platform on somebody else's machine — where the session's home
and workdir are, the ``cheese`` CLI on PATH, the event spool and the drainer
that empties it, the tunnel and the preview helper, and the supervisor that
owns all of them plus the agent process. The other is one harness: which
binary, which version, which config files, how a conversation resumes.

Only the second changes with the harness, and until this module existed the two
were the same 559-line f-string inside ``harness/claude_code``. A second harness
could only start by copying it, and the copy would drift the way four native
tool lists drifted.

So the skeleton lives here and the harness fills four holes. They are named for
WHEN they run, because that is the only thing this side knows about them:

``configure``   after ``cheese-environment.py`` is on disk: the harness's own
                config files, written before anything can read them.
``credentials`` after the platform CLI is on PATH, so this hole may report its
                own failure through ``cheese-hook``.
``prepare``     after ``cd`` into the workdir: find the binary, check its
                version, decide what resuming means, and leave the command in a
                shell variable.
``command``     that variable, named — ``"$AGENT"``. It is substituted into
                the supervisor's ``eval`` inside double quotes, so it has to
                be one shell word; a command spelled out here with quotes of
                its own would end the string it lands in.

The environment runner wraps whatever runs here (``CHEESE_ENVIRONMENT``), which
is why it is the supervisor's line and not a harness's: a project's setup and
startup scripts are the platform's promise about the machine, and they hold
whichever agent the room asked for.
"""

import hashlib
import json
from pathlib import Path

from app.domain.agent import (
    environment_runner,
    event_drain,
    forge_cli,
    machine_tunnel,
    preview_tunnel,
    toolchain,
)
from app.domain.agent.harness.launch import MachineLaunch, MachinePlace
from app.domain.agent.hook_forwarder import CHEESE_HOOK_SCRIPT

# The launcher below spells the platform's own directory literally, because the
# script is one long shell string and a name threaded through sixty paths would
# be harder to read than the paths it stands for. The name is
# `place.footprint_root()`, and `test_the_launcher_installs_only_inside_the_footprint`
# reads every write point out of the rendered script and holds it to that name.

# Starts the tunnel helper, and does NOT return until the helper it started has
# bound a port. Prints that port on stdout and nothing else; the caller exports
# it as HTTPS_PROXY for the agent it is about to start.
#
# The port belongs to the machine. Only its kernel knows which ports are free,
# so a helper with no port recorded binds port 0 and reports what it got. The
# backend used to derive one by hashing the room into a 2000-port window, and
# with ~50 rooms on one host two of them landed on the same number: the second
# helper died on EADDRINUSE, the first one answered the readiness check in its
# place, and the second room's `claude` sent everything it had — model calls,
# its Remote Control registration — through the first room's helper, on the
# first room's credential. That is why "ready" here means the helper THIS run
# started wrote its port file while still alive, and never that something
# answers on a port.
#
# The wait is the point. `claude` reads HTTPS_PROXY once at startup and makes its
# first request (the login/profile check) immediately, so a helper that is merely
# "starting" loses that race and the screen boots looking unauthenticated. Bounded
# rather than unbounded: if it cannot bind in five seconds it is not going to, and
# hanging the launch would be a worse failure than a loud one.
#
# The recorded port is tried first when a helper has to be started again, and a
# fresh one only when that bind fails. The launcher hands whichever port was
# printed to the agent it starts next, so either one is safe.
#
# Adopt-if-alive for the same reason the drainer does: a screen is reused across
# turns, and restarting a working helper each time would reset every in-flight
# connection. Adoption needs all four: the recorded pid alive, the stamp
# matching the helper on disk, the port file present, and that pid holding the
# LISTEN socket on that port.
# A pid that resolves proves only that SOME process holds that number — after a
# reboot, or on a box that has burnt through the pid space, that is a
# coincidence, and adopting on it leaves the port dead for the life of the
# screen with nothing anywhere reporting a fault.
#
# `nohup` is what makes the helper outlive a caller that returns. Measured
# 2026-08-30 on the dev box: fifteen topics whose helper was gone and whose
# `claude` had been dialling a dead port for days. Their `cheese-tunnel.log` said
# `tunnel listening` at the timestamp of the last launch, so this script HAD
# started a helper; it simply did not outlive the tmux window that ran the
# script, whose process group is torn down — SIGHUP — the moment the script
# returns.
CHEESE_TUNNEL_UP = """#!/bin/sh
PIDF="$HOME/.cheese/cheese-tunnel.pid"
STAMPF="$HOME/.cheese/cheese-tunnel.stamp"
PORTF="$HOME/.cheese/cheese-tunnel.port"
LOG="$HOME/.cheese/cheese-tunnel.log"
# Does pid $1 itself hold the LISTEN socket on port $2? Something answering there
# is not enough: after this room's helper died, the kernel may have handed its
# port to another room's helper. Linux answers from /proc; elsewhere lsof does.
# With neither, nothing is adopted and a helper of our own is started.
tunnel_owned() {
  python3 - "$1" "$2" <<'PROBEPY'
import os, subprocess, sys

pid, port = sys.argv[1], int(sys.argv[2])
if os.path.isdir("/proc/%s" % pid) and os.path.exists("/proc/net/tcp"):
    inodes = set()
    for fd in os.listdir("/proc/%s/fd" % pid):
        target = os.readlink("/proc/%s/fd/%s" % (pid, fd))
        if target.startswith("socket:["):
            inodes.add(target[len("socket:["):-1])
    for table in ("/proc/net/tcp", "/proc/net/tcp6"):
        if not os.path.exists(table):
            continue
        with open(table) as handle:
            for line in handle.readlines()[1:]:
                cols = line.split()
                local_port = int(cols[1].rsplit(":", 1)[1], 16)
                if cols[3] == "0A" and local_port == port and cols[9] in inodes:
                    raise SystemExit(0)
    raise SystemExit(1)
try:
    held = subprocess.run(
        ["lsof", "-nP", "-a", "-p", pid, "-iTCP:%d" % port, "-sTCP:LISTEN", "-t"],
        capture_output=True, text=True, timeout=5,
    ).stdout.split()
except (OSError, subprocess.TimeoutExpired):
    held = []
raise SystemExit(0 if pid in held else 1)
PROBEPY
}
# Adopt a live helper ONLY if it is running the helper we just wrote. The
# launcher rewrites cheese-tunnel.py on every launch, so a shipped fix would
# otherwise never reach a machine whose helper is still alive — it would keep
# serving the old code indefinitely, and nothing would look wrong.
WANT="$(cksum "$HOME/.cheese/cheese-tunnel.py" 2>/dev/null | cut -d" " -f1)"
HAVE="$(cat "$STAMPF" 2>/dev/null || true)"
PID="$(cat "$PIDF" 2>/dev/null || true)"
PORT="$(cat "$PORTF" 2>/dev/null || true)"
case "$PORT" in ''|*[!0-9]*) PORT="" ;; esac
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  if [ -n "$WANT" ] && [ "$WANT" = "$HAVE" ] && [ -n "$PORT" ] \\
    && tunnel_owned "$PID" "$PORT" 2>/dev/null; then
    printf '%s\\n' "$PORT"
    exit 0
  fi
  # Different code, or a pid that is alive without holding its port:
  # retire it. In-flight turns see one connection reset, which claude retries;
  # a permanently stale helper does not heal at all.
  kill "$PID" 2>/dev/null || true
fi
NEWF="$PORTF.new.$$"
# Start a helper on port $1 and wait for it. Exit 0: it is alive and wrote its
# port file. 3: it exited, which is how a failed bind looks. 1: timed out.
# The wait runs in python3, NOT with bash's /dev/tcp: this script is invoked as
# `sh`, and dash has no /dev/tcp. python3 is not an extra dependency here; the
# helper itself is written in it.
start_helper() {
  rm -f "$NEWF"
  nohup python3 "$HOME/.cheese/cheese-tunnel.py" \\
    --port "$1" --port-file "$NEWF" --url "$CHEESE_TUNNEL_URL" \\
    --token-file "$HOME/.cheese/cheese-tunnel.token" \\
    >>"$LOG" 2>&1 &
  NEWPID=$!
  python3 - "$NEWPID" "$NEWF" <<'WAITPY'
import os, sys, time

pid, path = int(sys.argv[1]), sys.argv[2]


def alive():
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # An exited child the shell has not reaped yet still answers kill -0.
    try:
        with open("/proc/%d/stat" % pid) as handle:
            return handle.read().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return True


deadline = time.monotonic() + 5.0
while time.monotonic() < deadline:
    if not alive():
        raise SystemExit(3)
    if os.path.exists(path):
        raise SystemExit(0)
    time.sleep(0.02)
raise SystemExit(1)
WAITPY
}
: >"$LOG"
start_helper "${PORT:-0}"
RC=$?
if [ "$RC" = 3 ] && [ -n "$PORT" ]; then
  # Something else holds the recorded port now. Let the kernel choose.
  start_helper 0
  RC=$?
fi
if [ "$RC" != 0 ]; then
  kill "$NEWPID" 2>/dev/null || true
  rm -f "$NEWF" "$PIDF"
  echo "cheese-tunnel-up: the tunnel helper did not come up; $LOG ends:" >&2
  tail -n 5 "$LOG" >&2
  exit 1
fi
mv "$NEWF" "$PORTF"
echo "$NEWPID" > "$PIDF"
printf '%s\\n' "$WANT" > "$STAMPF"
cat "$PORTF"
"""


# Brings 运行环境预览's helper up, and does nothing at all until an agent has asked
# for a preview. The port file is that ask: `cheese serve` writes it and then runs
# this script, and every later launch re-runs it so a preview that was declared
# once survives a helper's death (a machine reboot, a killed process) without the
# agent having to declare it again.
#
# Adopt-if-alive on the same cksum, for the same reason the tunnel helper does:
# the launcher rewrites cheese-preview.py on every launch, so a shipped fix would
# otherwise never reach a machine whose helper is still running — it would keep
# serving the old code indefinitely with nothing looking wrong.
#
# No readiness wait here (unlike the tunnel's): nothing on this machine is
# blocked on the tunnel being up. The one caller that needs it up — `cheese serve`
# declaring the preview — waits on the BACKEND side, where the helper's arrival is
# actually observable.
CHEESE_PREVIEW_UP = """#!/bin/sh
# $1, optional: the port.
# `cheese serve`
# passes it and nothing else does, which is what keeps the file layout of the
# preview helper entirely inside the launcher — the CLI knows only this script.
PORTF="$HOME/.cheese/cheese-preview.port"
PIDF="$HOME/.cheese/cheese-preview.pid"
STAMPF="$HOME/.cheese/cheese-preview.stamp"
if [ -n "$1" ]; then
  printf '%s\\n' "$1" > "$PORTF.tmp" && mv "$PORTF.tmp" "$PORTF"
fi
[ -s "$PORTF" ] || exit 0
[ -n "${CHEESE_PREVIEW_URL:-}" ] || exit 0
WANT="$(cksum "$HOME/.cheese/cheese-preview.py" 2>/dev/null | cut -d" " -f1)"
HAVE="$(cat "$STAMPF" 2>/dev/null || true)"
PID="$(cat "$PIDF" 2>/dev/null || true)"
if [ -n "$PID" ] && kill -0 "$PID" 2>/dev/null; then
  if [ -n "$WANT" ] && [ "$WANT" = "$HAVE" ]; then
    exit 0
  fi
  kill "$PID" 2>/dev/null || true
fi
python3 "$HOME/.cheese/cheese-preview.py" \\
  --url "$CHEESE_PREVIEW_URL" \\
  --token-file "$HOME/.cheese/cheese-preview.token" \\
  --port-file "$PORTF" \\
  >"$HOME/.cheese/cheese-preview.log" 2>&1 &
echo $! > "$PIDF"
printf '%s\\n' "$WANT" > "$STAMPF"
"""


def toolchain_fetcher() -> str:
    """Place the room's document toolchain on this machine, once per machine.

    These are capabilities, not dependencies — a room that never writes a
    document needs none of them — so nothing here may fail a launch or delay
    one. It runs detached behind a directory lock, and a room that asks for
    pandoc while the fetch is still running finds it missing and says so, which
    is the honest answer and the one `skills/documents` now gives.

    Under $REAL_HOME, not the session home: the tools belong to the MACHINE.
    Every room on it shares one copy, and the copy outlives any of them. The
    version is in the path for the same reason claude's pin is — a bump lands
    beside the old one and is fetched on the next launch, with no
    re-provisioning and nothing to uninstall.

    TYPST_FONT_PATHS, and deliberately NOT TYPST_IGNORE_SYSTEM_FONTS. Ours are
    found first either way (typst resolves --font-path before system fonts), so
    ignoring the machine's own fonts would only take away faces a user
    legitimately has. Which family a document names is the skill's business,
    not an environment variable's.
    """
    fonts_pin = toolchain.fonts_pin()
    places = "\n".join(
        f"cheese_place {tool} {version} {kind} {name}"
        for tool, version, kind, name in toolchain.PLACEMENTS
    )
    fetcher = f"""#!/bin/sh
  case "$(uname -m)" in
    x86_64|amd64) _tarch=x64 ;;
    aarch64|arm64) _tarch=arm64 ;;
    *) exit 0 ;;
  esac
  # Git Bash on Windows reports MINGW64_NT-<version>. There a binary is found
  # and linked by its .exe name, and `ln` is a hard link: an MSYS symlink is a
  # copy unless the machine allows real ones, and a copy of pandoc is 230MB.
  _exe=""
  _ln="ln -sf"
  case "$(uname -s)" in
    Darwin) _tos=darwin ;;
    MINGW*|MSYS*|CYGWIN*) _tos=windows; _exe=.exe; _ln="ln -f" ;;
    *) _tos=linux ;;
  esac
  _tplat="$_tos-$_tarch"
  mkdir -p "$CHEESE_TOOLCHAIN/bin" || exit 0
  # `mkdir` is the lock: it is atomic on every filesystem a machine might use,
  # and two rooms starting at once must not both pull 111MB. A stale lock costs
  # the next launch a retry, never a wedged room, because nothing waits on this.
  mkdir "$CHEESE_TOOLCHAIN/.fetching" 2>/dev/null || exit 0
  trap 'rmdir "$CHEESE_TOOLCHAIN/.fetching" 2>/dev/null' EXIT INT TERM

  cheese_place() {{
    # tool version kind name
    _dest="$CHEESE_TOOLCHAIN/$1/$2"
    if [ "$3" = font ]; then
      _dest="$CHEESE_TOOLCHAIN/fonts/{fonts_pin}"
      [ -s "$_dest/$4" ] && return 0
    else
      if [ -x "$_dest/$4$_exe" ]; then
        $_ln "$_dest/$4$_exe" "$CHEESE_TOOLCHAIN/bin/$4$_exe"
        return 0
      fi
    fi
    _work="$CHEESE_TOOLCHAIN/.part.$1"
    rm -rf "$_work"
    mkdir -p "$_work" || return 0
    if ! curl -fsSL --retry 3 --retry-delay 2 -m 900 \\
        "${{CHEESE_API%/}}/connector/toolchain/$1/$_tplat/artifact" \\
        -o "$_work/a"; then
      rm -rf "$_work"
      return 0
    fi
    mkdir -p "$_dest" || {{ rm -rf "$_work"; return 0; }}
    if [ "$3" = font ]; then
      mv "$_work/a" "$_dest/$4"
      rm -rf "$_work"
      return 0
    fi
    # The archive's inside is the vendor's business and it changes between
    # releases, so the binary is found by name rather than by a path spelled
    # out here. tar reads .tar.gz and .tar.xz; macOS pandoc ships a zip.
    ( cd "$_work" && tar -xf a 2>/dev/null ) \\
      || ( cd "$_work" && unzip -q a 2>/dev/null ) \\
      || {{ rm -rf "$_work"; return 0; }}
    _found="$(find "$_work" -type f -name "$4$_exe" 2>/dev/null | head -n 1)"
    if [ -z "$_found" ]; then
      rm -rf "$_work"
      return 0
    fi
    chmod +x "$_found" 2>/dev/null || true
    mv "$_found" "$_dest/$4$_exe" || {{ rm -rf "$_work"; return 0; }}
    rm -rf "$_work"
    $_ln "$_dest/$4$_exe" "$CHEESE_TOOLCHAIN/bin/$4$_exe"
  }}

{places}
"""
    return fetcher


def toolchain_block() -> str:
    fonts_pin = toolchain.fonts_pin()
    fetcher = toolchain_fetcher()
    return f"""CHEESE_TOOLCHAIN="$REAL_HOME/.cheese/toolchain"
export CHEESE_TOOLCHAIN
export PATH="$CHEESE_TOOLCHAIN/bin:$PATH"
export TYPST_FONT_PATHS="$CHEESE_TOOLCHAIN/fonts/{fonts_pin}"
if [ -n "${{CHEESE_API:-}}" ]; then
  cat > "$HOME/.cheese/cheese-toolchain" <<'TOOLCHAIN'
{fetcher}TOOLCHAIN
  chmod +x "$HOME/.cheese/cheese-toolchain"
  # `( cmd & )` — a double fork, and the parentheses are the whole point.
  # The fetch belongs to the machine rather than the session that first needed it.
  # The inner `&` runs it beyond the short-lived subshell, and `nohup` keeps it
  # independent of the terminal. It can finish or stop with the machine without
  # sending either outcome to a room.
  ( nohup "$HOME/.cheese/cheese-toolchain" </dev/null >/dev/null 2>&1 & )
fi
"""


def state_dir(project_id, resource_id, harness: str, agent_handle: str) -> str:
    """一个 harness 在那台机器上放 state 的地方，写成连接器认的那种路径。

    The literal ``$HOME`` is the connector's to expand, to the MACHINE OWNER's
    home: a session's own home is rebuilt whenever its screen is, and what lives
    here — the journal, the record of which inputs were already accepted, the
    identity of the socket derived from this very string — has to outlive that.

    One formula, because two ends read it: the channel writes it down, and
    whatever reaches the session later derives the socket from the same string.
    Two copies of this is how four native-tool lists happened.
    """
    return (
        f"$HOME/.cheese/harness/{project_id}/{resource_id}/{harness}/"
        + hashlib.sha256(agent_handle.encode()).hexdigest()
    )


def screen_launch(
    place: MachinePlace,
    spec: MachineLaunch,
    *,
    hook_url: str,
    token: str,
) -> tuple[list[str], dict[str, str]]:
    """一次设备启动的两半，合到一起：``(command, env)``。

    The channel says where, the harness said what, and this is the one place
    the two meet — so that a test can reach the same result a device gets
    without standing one up, rather than re-deriving the composition and then
    agreeing with itself.
    """
    return (
        [
            "bash",
            "-lc",
            launch_script(
                configure=spec.configure,
                credentials=spec.credentials,
                prepare=spec.prepare,
                command=spec.command,
            ),
        ],
        {**screen_env(place, hook_url=hook_url, token=token), **spec.env},
    )


def screen_env(
    place: MachinePlace,
    *,
    hook_url: str,
    token: str,
) -> dict[str, str]:
    """一个会话环境里平台那一半：换哪个 harness 都一样的那些变量。

    The session takes ONE environment and both halves have to be in it, so a
    channel merges what the harness answered onto this. Everything here is read
    by the script above, by the ``cheese`` CLI, or by the drainer — never by a
    particular agent binary.
    """
    env = {
        "CHEESE_HOOK_URL": hook_url,
        "CHEESE_TOKEN": token,
        "CHEESE_HOME": place.home,
        "CHEESE_WORK": place.workdir,
    }
    if place.store:
        # Absent rather than empty, for the reason spelled out below: a screen
        # with no project (a probe, a fixture) has no project store, and the
        # launcher must not point a package manager at `.cheese/store/`.
        env["CHEESE_STORE"] = place.store
    # `CHEESE_API` is the backend root as-is: it maps 1:1 onto it (see
    # settings.connector_public_base) and every route is bare since #370 step 2,
    # so the CLI's base IS that base. Appending another `/api` was right only
    # while the 2.0 routes carried their own prefix; afterwards it injected
    # `<origin>/api/api` and every `cheese` command in a sandbox 404'd.
    #
    # Absent rather than empty. A screen with no topic (a probe, a fixture) has
    # no `cheese` CLI context to give, and an empty value is not the same answer
    # as no value: the launcher and the CLI both branch on whether the variable
    # is set at all.
    for name, value in (
        ("CHEESE_API", place.api_base),
        ("CHEESE_PROJECT", place.project_id),
        ("CHEESE_TOPIC", place.topic_id),
        ("CHEESE_AUTHOR", place.agent_handle),
    ):
        if value:
            env[name] = value
    if place.execution_target is not None:
        env["CHEESE_EXECUTION_TARGET"] = json.dumps(place.execution_target)
    if place.project_id:
        # Who the turn's commits belong to (workspace/identity.py). Absent, the
        # launcher falls back to 芝士 — the same default the in-repo snapshot
        # path uses, so both surfaces agree.
        env["CHEESE_GIT_AUTHOR_NAME"] = place.agent_handle
        env["CHEESE_GIT_AUTHOR_EMAIL"] = f"{place.agent_handle}@agent.cheese.local"
        env["GIT_AUTHOR_NAME"] = place.agent_handle
        env["GIT_AUTHOR_EMAIL"] = env["CHEESE_GIT_AUTHOR_EMAIL"]
        env["GIT_COMMITTER_NAME"] = "芝士"
        env["GIT_COMMITTER_EMAIL"] = "cheese@zhishi.local"
    return env


def build_drain_script() -> str:
    source = Path(event_drain.__file__).read_text(encoding="utf-8")
    return '#!/bin/sh\nexec python3 - "$0" "$@" <<\'PY\'\n' + source + "\nPY\n"


def launch_script(
    *,
    configure: str = "",
    credentials: str = "",
    prepare: str = "",
    command: str,
) -> str:
    """The launcher a device runs, with this harness's four holes filled.

    Every value the harness needs beyond these reaches it as environment: the
    channel builds one environment for the session and both halves read it.
    """
    drain_script = build_drain_script()
    cli_source = (Path(__file__).resolve().parents[3] / "sandbox" / "cheese").read_text(
        encoding="utf-8"
    )
    forge_source = Path(forge_cli.__file__).read_text()
    # Shipped by reading each module's own bytes rather than by keeping a second
    # copy here: they are real, linted, unit-tested modules precisely so there is
    # only one version of them to be wrong.
    environment_helper = (
        Path(environment_runner.__file__).read_text().rstrip("\n") + "\n"
    )
    tunnel_helper = Path(machine_tunnel.__file__).read_text().rstrip("\n") + "\n"
    preview_helper = Path(preview_tunnel.__file__).read_text().rstrip("\n") + "\n"
    tunnel_up = CHEESE_TUNNEL_UP
    preview_up = CHEESE_PREVIEW_UP
    toolchain = toolchain_block()
    return f"""set -e
# CHEESE_HOME/CHEESE_WORK arrive with a LITERAL "$HOME/..." placeholder (the
# server cannot know the device user's home). Substitute the REAL home first —
# treating it as a relative path only worked by accident from a writable cwd
# (a fresh service cwd of / made mkdir die with "cannot create '$HOME'").
# POSIX-only from here: the connector's tmux joins argv with spaces and
# re-parses through /bin/sh (dash on Debian/Ubuntu) — bashisms die silently.
REAL_HOME="$HOME"
# Bash exposes this clock without spawning date. Other shells skip diagnostics.
# Append across relaunches; only phase names and timestamps enter this file.
cheese_launch_phase() {{
  [ -n "${{EPOCHREALTIME:-}}" ] || return 0
  [ -d "$REAL_HOME/.cheese/launch" ] || return 0
  printf '%s %s\\n' "$EPOCHREALTIME" "$1" \\
    >> "$REAL_HOME/.cheese/launch/$CHEESE_TOPIC.timing" 2>/dev/null || :
}}
cheese_launch_phase started
CH="${{CHEESE_HOME:?Cheese session home is required}}"
CW="${{CHEESE_WORK:?Cheese work directory is required}}"
case "$CH" in "\\$HOME"*) CH="$REAL_HOME${{CH#\\$HOME}}";; esac
case "$CW" in "\\$HOME"*) CW="$REAL_HOME${{CW#\\$HOME}}";; esac
# Every room of this project on this machine installs the same lockfile, so
# point the package managers at one store outside any room's HOME. uv and pnpm
# then HARDLINK out of it instead of copying, which they can only do while the
# store and the tree share a filesystem — `.cheese/store` and `.cheese/work`
# both hang off the machine's own home, so they do by construction and there is
# nothing to check at runtime. The last two hardlink nothing and are here for
# the plainer half of the saving: not downloading the same archive again.
#
# `uv-python` is the exception in this list and the one a cleanup must not
# touch: it is not a cache but the INTERPRETER a venv points at. `.venv/bin/python`
# is an absolute symlink into it and `pyvenv.cfg`'s `home =` names it, so
# deleting it leaves every venv in the project pointing at nothing until a plain
# `uv run` rebuilds them. Sweeping `uv-cache` costs a download; sweeping
# `uv-python` breaks rooms until they repair themselves.
#
# Nothing here needs HOME to have been swapped yet, only REAL_HOME.
#
# `mkdir` guarded because `set -e` is in force and nothing here is worth failing
# a launch over — a store that cannot be made leaves every tool on its own
# default, which is exactly where they were before this existed. A project that
# sets any of these itself still wins: `cheese-environment.py` layers the room's
# configured variables over the inherited environment, not under it.
CS="${{CHEESE_STORE:-}}"
case "$CS" in "\\$HOME"*) CS="$REAL_HOME${{CS#\\$HOME}}";; esac
if [ -n "$CS" ] && mkdir -p "$CS" 2>/dev/null; then
  export UV_CACHE_DIR="$CS/uv-cache"
  export UV_PYTHON_INSTALL_DIR="$CS/uv-python"
  export npm_config_store_dir="$CS/pnpm-store"
  export npm_config_cache="$CS/npm-cache"
  export PIP_CACHE_DIR="$CS/pip-cache"
  # Re-export it RESOLVED. What arrived carries a literal `$HOME` the backend
  # could not expand, and `cheese-environment.py` reads this variable to find
  # the prefix it installs the project's tools into — a program that gets
  # "$HOME/.cheese/store/..." as a path would make a directory called `$HOME`.
  export CHEESE_STORE="$CS"
  CSLIVE=1
fi
export HOME="$CH" CHEESE_WORK="$CW"
mkdir -p "$HOME" "$CHEESE_WORK"
# Canonicalize to absolutes (resolve symlinks) so nothing depends on cwd —
# a tmux-hosted agent runs from a fresh server with a cwd of its own.
export HOME="$(cd "$HOME" && pwd -P)"
export CHEESE_WORK="$(cd "$CHEESE_WORK" && pwd -P)"
# With the stores redirected above, the copies these tools left in the room's
# own HOME are read by nothing. Reclaim them — a room created before the store
# existed holds them until it is retired, and nothing retires an idle room.
#
# What that is worth is not uniform, measured 2026-09-17:
#
#   ~/.npm/_cacache   792MiB   100% reclaimed
#   ~/.cache/pip      240MiB   100% reclaimed
#   ~/.cache/uv       1.5GiB    12% reclaimed
#   pnpm store        3.0GiB     2% reclaimed
#
# The first two are download caches that nothing links to, so every byte comes
# back. uv's and pnpm's are content-addressed stores the install HARDLINKS out
# of: 98% of their bytes are also in a `.venv`/`node_modules` that stays, so
# deleting them drops a link count and frees nothing. Swept anyway — the
# remainder is real and nothing can break — but not free either: the next
# install in that room re-resolves and re-fetches whatever the shared store has
# not seen. Once per room.
#
# NOT `$HOME/.local/share/uv`, nor its Darwin twin. That holds the managed
# INTERPRETER, which a venv reaches by absolute symlink with `pyvenv.cfg`'s
# `home =` naming it; deleting it leaves every venv in the room pointing at
# nothing until a plain `uv run` rebuilds them.
#
# Two guards, and the second is the load-bearing one. CSLIVE says the redirect
# above actually happened — without it these directories are still the live
# ones. `$HOME != $REAL_HOME` says we are in a room at all, and not standing in
# the machine owner's own home, which is whose `~/.cache` this would be.
#
# Detach this cleanup for the same reason as the toolchain fetch below: it
# belongs to the machine rather than the session that triggered it.
if [ -n "${{CSLIVE:-}}" ] && [ "$HOME" != "$REAL_HOME" ]; then
  if [ "$(uname -s)" = Darwin ]; then
    ( nohup rm -rf "$HOME/Library/Caches/uv" "$HOME/Library/Caches/pip" \\
        "$HOME/.npm/_cacache" "$HOME/Library/pnpm/store" \\
        </dev/null >/dev/null 2>&1 & )
  else
    ( nohup rm -rf "$HOME/.cache/uv" "$HOME/.cache/pip" \\
        "$HOME/.npm/_cacache" "$HOME/.local/share/pnpm/store" \\
        </dev/null >/dev/null 2>&1 & )
  fi
fi
# The platform's own directory inside the session home. It used to be
# $HOME/.claude — the platform squatting in one harness's directory, which
# read as deliberate to everyone who came after it. A harness that wants a
# directory of its own makes it in `configure`.
mkdir -p "$HOME/.cheese"
cat > "$HOME/.cheese/cheese-environment.py" <<'CHEESE_ENV_PY'
{environment_helper}CHEESE_ENV_PY
{configure}cat > "$HOME/.cheese/cheese-hook" <<'SH'
{CHEESE_HOOK_SCRIPT}SH
chmod +x "$HOME/.cheese/cheese-hook"
# Ship the platform CLI with the launcher over the existing device connection.
# A separate public HTTP download added 0.39-1.24s to measured launches and
# could block each launch for its 10s timeout.
cat > "$HOME/.cheese/cheese" <<'CHEESE_PLATFORM_CLI'
{cli_source}CHEESE_PLATFORM_CLI
chmod +x "$HOME/.cheese/cheese"
cat > "$HOME/.cheese/gh" <<'CHEESE_FORGE_CLI'
{forge_source}CHEESE_FORGE_CLI
cp "$HOME/.cheese/gh" "$HOME/.cheese/fj"
chmod +x "$HOME/.cheese/gh" "$HOME/.cheese/fj"
cat > "$HOME/.cheese/cheese-tunnel.py" <<'TUNNELPY'
{tunnel_helper}TUNNELPY
export PATH="$HOME/.cheese:$PATH"
{toolchain}cheese_launch_phase files_written
{credentials}\
# Durable event delivery on the device: cheese-hook spools every hook and (via
# CHEESE_HOOK_SPOOL_ONLY) skips its own inline curl, so the cheese-drain script
# is the sole sender — it retries each spooled event until the backend DURABLY
# accepts it (code:200 = the backend wrote the event to the topic's server-side
# spool; anything else leaves this machine's copy in place, which is the only
# one there is), so a link/backend outage never drops an event. A 24h age cap
# stops an unreachable backend from accumulating retries forever. The backend
# dedups re-deliveries by event-id.
#
# The supervisor below owns the drainer for a newly started session.
export CHEESE_HOOK_SPOOL="$HOME/.cheese/cheese-spool"
export CHEESE_HOOK_SPOOL_ONLY=1
mkdir -p "$CHEESE_HOOK_SPOOL"
cat > "$HOME/.cheese/cheese-drain" <<'DRAIN'
{drain_script}DRAIN
chmod +x "$HOME/.cheese/cheese-drain"
cat > "$HOME/.cheese/cheese-drain.env.tmp" <<DRAINENV
CHEESE_HOOK_SPOOL="$CHEESE_HOOK_SPOOL"
CHEESE_HOOK_URL="$CHEESE_HOOK_URL"
CHEESE_TOKEN="$CHEESE_TOKEN"
DRAINENV
mv "$HOME/.cheese/cheese-drain.env.tmp" "$HOME/.cheese/cheese-drain.env"
# The tunnel helper, for a machine that cannot reach the meter's listener
# directly. Written on EVERY launch, token included: the helper re-reads the
# token per connection, so replacing this file is how a refreshed credential
# reaches a still-running helper (#385's shape, one layer down).
if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
  # Use the place-scoped CONNECT credential, including its RC claim. The hook
  # token can have project scope; the machine OAuth ticket is never a tunnel
  # credential. A missing CONNECT token must not fall back to either one.
  cat > "$HOME/.cheese/cheese-tunnel.token.tmp" <<TUNNELTOK
$CHEESE_CONNECT_TOKEN
TUNNELTOK
  chmod 600 "$HOME/.cheese/cheese-tunnel.token.tmp"
  mv "$HOME/.cheese/cheese-tunnel.token.tmp" "$HOME/.cheese/cheese-tunnel.token"
  cat > "$HOME/.cheese/cheese-tunnel-up" <<'TUNNELUP'
{tunnel_up}TUNNELUP
  chmod +x "$HOME/.cheese/cheese-tunnel-up"
fi
# 运行环境预览's helper. Written on EVERY launch, token included and for the same
# reason as the tunnel's: the helper re-reads the token per connection, so
# replacing this file is how a refreshed credential reaches a still-running one.
# Nothing is STARTED here — the up script is a no-op until an agent has declared
# a port with `cheese serve`, so a machine that never previews anything pays for
# no process.
if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
  cat > "$HOME/.cheese/cheese-preview.py" <<'PREVIEWPY'
{preview_helper}PREVIEWPY
  cat > "$HOME/.cheese/cheese-preview.token.tmp" <<PREVIEWTOK
$CHEESE_TOKEN
PREVIEWTOK
  chmod 600 "$HOME/.cheese/cheese-preview.token.tmp"
  mv "$HOME/.cheese/cheese-preview.token.tmp" "$HOME/.cheese/cheese-preview.token"
  cat > "$HOME/.cheese/cheese-preview-up" <<'PREVIEWUP'
{preview_up}PREVIEWUP
  chmod +x "$HOME/.cheese/cheese-preview-up"
  # The ONE thing `cheese serve` needs to know about the preview helper. Exported
  # rather than reconstructed on the CLI's side: the server cannot know the
  # device user's home, and a path written down twice is a path that drifts.
  export CHEESE_PREVIEW_UP="$HOME/.cheese/cheese-preview-up"
fi
cd "$CHEESE_WORK"
{prepare}# The connector owns the terminal session. This shell owns its children.
if [ -n "${{TMUX:-}}" ]; then
  CHEESE_TMUX_SOCK="${{TMUX%%,*}}"
  SESSION="$(tmux -S "$CHEESE_TMUX_SOCK" display-message -p -t "$TMUX_PANE" '#S')"
  python3 -c 'import json,sys; json.dump(sys.argv[1:], open(sys.argv[3], "w"))' \\
    "$CHEESE_TMUX_SOCK" "$SESSION" "$HOME/.cheese/environment-session.json"
fi
ENVIRONMENT_CMD=""
if [ -n "${{CHEESE_ENVIRONMENT:-}}" ]; then
  ENVIRONMENT_CMD="python3 \\"$HOME/.cheese/cheese-environment.py\\" "
fi
AGENT_PID=""
DRAIN_PID=""
cleanup() {{
  trap '' HUP INT TERM
  [ -z "$AGENT_PID" ] || kill "$AGENT_PID" 2>/dev/null || true
  [ -z "$DRAIN_PID" ] || kill "$DRAIN_PID" 2>/dev/null || true
  # The drainer is tethered to this shell and cannot keep the session alive.
  # A delayed TERM handler must not delay the foreground agent's result.
  [ -z "$AGENT_PID" ] || wait "$AGENT_PID" 2>/dev/null || true
}}
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
trap cleanup EXIT
if [ -n "${{CHEESE_TUNNEL_URL:-}}" ]; then
  # The machine chose the port, so only this line can tell the agent which.
  TUNNEL_PORT="$(sh "$HOME/.cheese/cheese-tunnel-up")" || exit 1
  export HTTPS_PROXY="http://127.0.0.1:$TUNNEL_PORT"
fi
if [ -n "${{CHEESE_PREVIEW_URL:-}}" ]; then
  sh "$HOME/.cheese/cheese-preview-up" || exit 1
fi
CHEESE_DRAIN_TETHER=$$ sh "$HOME/.cheese/cheese-drain" >/dev/null 2>&1 &
DRAIN_PID=$!
# Preserve input before POSIX sh redirects an asynchronous command's fd 0.
exec 3<&0
eval "exec $ENVIRONMENT_CMD{command}" <&3 3<&- &
AGENT_PID=$!
RESULT=0
wait "$AGENT_PID" || RESULT=$?
AGENT_PID=""
exit "$RESULT"

"""
