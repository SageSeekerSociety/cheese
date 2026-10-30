"""What the runtime and every piece shipped beside it read.

The platform primitives (Windows and POSIX alike), the constants the protocol
and the command registry are held to, where a state's service puts its sockets
and how a client dials them, and the small helpers the rest of the release
reaches — `beside` above all, which is how a sibling that must not import
anything is loaded (`portable`, `darwin`, `mcp_process`, `machine_files`,
`machine_git`, `repo_search`, `predecessor`).

Split out of `runtime.py` on 2026-10-16 with `runtime_commands`,
`runtime_context` and `runtime_calls`. `runtime.py` holds these names again
(re-exported) and is the only one of the five that is an entry point.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import re
import runpy
import shutil
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

if sys.platform != "win32":
    import fcntl

    NOFOLLOW = os.O_NOFOLLOW
    BINARY = 0
    pwrite = os.pwrite
else:
    # No symlink flag to open with: `workflow_path` has already refused a path
    # with a link anywhere in it. BINARY because a descriptor Windows opens in
    # text mode writes every \n as \r\n.
    NOFOLLOW = 0
    BINARY = os.O_BINARY

    def pwrite(fd, data, offset):
        os.lseek(fd, offset, os.SEEK_SET)
        return os.write(fd, data)


# What this runtime reads from its own release, by its path there, with the
# file it is built from in a checkout (under `backend/`). The launch ships these
# (`launch.file_sources`) and every copy of this runtime installs them the same
# way (`tests/support/executor_release.py`): machine and fixture get the same.
_OWN = (
    "runtime",
    # The four the runtime's own bodies moved into on 2026-10-16. It is one
    # program shipped whole, so every module it imports travels with it.
    "runtime_core",
    "runtime_commands",
    "runtime_context",
    "runtime_calls",
    "portable",
    "darwin",
    "mcp_process",
    "machine_files",
    "machine_git",
    "repo_search",
    "executor_download",
)
RELEASE_FILES = {
    **{
        f"remote-execution/{name}.py": (
            f"app/domain/agent/harness/claude_code/remote_execution/{name}.py"
        )
        for name in (*_OWN, "predecessor")
    },
    "remote-execution/project_hooks.py": "app/domain/agent/project_hooks.py",
    "remote-execution/package_mirrors.py": "app/domain/agent/package_mirrors.py",
    "remote-execution/worker_quota.py": "app/domain/agent/worker_quota.py",
    "remote-execution/raster_images.py": "app/core/raster_images.py",
    "remote-execution/cli_worker.py": "app/domain/agent/cli_worker.py",
    "remote-execution/forge_proxy.py": "app/domain/agent/forge_proxy.py",
    "cheese": "sandbox/cheese",
}

NATIVE_TOOLS = {
    "Read",
    "Edit",
    "Write",
    "Bash",
    "NotebookEdit",
    "TaskStop",
}

# A command the central session's shell prefix started (`shell`) is read by
# that prefix until it ends. One nobody has read for this long lost its reader
# for good — the session ended while the link to this machine was down — so it
# is stopped; a finished one nobody collected is forgotten. Longer than any
# link drop a deploy of the device connection causes, which the prefix waits
# out and then reattaches.
COMMAND_ABANDONED_S = 600.0
# The record of a command whose output the session keeps in a file of its own
# (`keep`) holds the same bytes, which a later command reads here; it stays
# until the session says it dropped that file, or this long after it ended.
OUTPUT_KEPT_S = 86400.0
# How long an executor with no request and nothing of its own running waits
# before it ends what its commands left behind and stops (`idle_stop`). Taking
# the session's hands starts it again — at a session's launch
# (`central_provider._starts_on_machine`) or at its next tool call
# (`session_work._start_executor` pings, finds nobody, installs); a call that
# takes no hands, such as a turn's Stop checkpoint, finds none until one has.
# 30 minutes is three times the 600 s after which the
# platform destroys an idle sandbox it records (`cloud_sandbox_idle_stop_s`) and
# a runner lets an idle session go (`driven.runner.IDLE_EXIT_S`): where those
# apply they act first, and this is the backstop for a home nothing records. It
# is also long enough that a turn's own pauses — the model thinking, a subagent
# working off the machine, a card waiting on CI — do not cost a cold start.
# Read through `CHEESE_EXECUTOR_IDLE_EXIT_S` in `serve` so a test need not wait
# half an hour; nothing on a machine sets it.
EXECUTOR_IDLE_EXIT_S = 1800.0
# How often an executor looks whether it has sat idle that long.
EXECUTOR_IDLE_CHECK_S = 30.0
# The stops a session's host sends, by their POSIX numbers: SIGHUP, SIGINT,
# SIGKILL, SIGTERM. Not spelled through `signal`: Windows has no SIGHUP or
# SIGKILL, so there every stop failed on the name before `signal_command`,
# which stops the whole tree on Windows whatever the number.
STOP_SIGNALS = frozenset({1, 2, 9, 15})
# The longest one `shell` read waits for output before answering with none.
COMMAND_READ_WAIT_S = 25.0
# The most one `shell` read answers with per stream, before base64.
COMMAND_READ_BYTES = 1024 * 1024
# What the executor's own Bash (Codex, platform commands) hands back inline,
# as the build's own Bash tool does; the rest stays in the task's output file.
BASH_INLINE_CHARS = 30_000
COMMAND_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")


def stamp():
    # This shipped script also runs on hosts with Python 3.9.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


@functools.cache
def beside(name):
    """A module shipped beside this file (`RELEASE_FILES`): Windows and macOS
    primitives (`portable`, `darwin`), the stdio MCP client, the files and git
    pi's tools take."""
    return runpy.run_path(str(Path(__file__).with_name(name + ".py")))


@functools.cache
def project_hooks():
    """The project's tool hooks, by the rules pi's runner follows too
    (`app/domain/agent/project_hooks.py`). Shipped beside this file; read from
    the source tree when this runs from a checkout."""
    beside = Path(__file__).with_name("project_hooks.py")
    if not beside.exists():
        source = RELEASE_FILES["remote-execution/project_hooks.py"]
        beside = Path(__file__).resolve().parents[6] / source
    return runpy.run_path(str(beside))


def lock(file, blocking=True):
    if sys.platform == "win32":
        beside("portable")["lock"](file, blocking)
    else:
        fcntl.flock(file, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)


def resolve_program(argv):
    return beside("portable")["which"](argv) if sys.platform == "win32" else argv


def socket_path(state):
    """Where a client finds this state's service: the address it dials, or on
    Windows the file naming its port and token. It exists only while the
    service may.

    On POSIX the address is a link to the socket in `socket_directory`. The
    connector derives this same name (`cli/internal/host/executor_unix.go`)."""
    if sys.platform == "win32":
        return str(Path(state) / "executor.endpoint")
    return socket_directory(state) + ".sock"


def socket_directory(state):
    """The directory this state's service binds its sockets in. A sandboxed
    executor (`confinement.sandbox_argv`) is shown this one directory of the
    machine's /tmp and nothing else there, so its sockets cannot sit beside
    every other room's."""
    digest = hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}"


def platform_socket_directory(state):
    """The directory of the platform socket the connector serves for this
    state (`cli/internal/host/machine.go` names the same one): beside
    `socket_directory`, and shown to the sandbox read-only. What the sandbox
    asks of the platform it asks there, and the connector says whose it is by
    which socket it came in on."""
    digest = hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-platform-{os.getuid()}-{digest}"


def connect(state):
    if sys.platform == "win32":
        # A missing file is FileNotFoundError and a port nobody listens on is
        # ConnectionRefusedError: the two a Unix socket gives for the same cases.
        endpoint = json.loads(Path(socket_path(state)).read_text())
        connection = socket.create_connection(("127.0.0.1", endpoint["port"]))
        connection.sendall(endpoint["token"].encode() + b"\n")
        return connection
    connection = socket.socket(socket.AF_UNIX)
    connection.connect(socket_path(state))
    return connection


def claude_shell(env):
    """The shell Claude Code runs its commands in on a machine with this
    environment, chosen as the pinned build chooses it (held to it by
    `scripts/remote_execution/mcp_contract.py`). On Windows, the Git Bash it is
    pointed at; elsewhere `CLAUDE_CODE_SHELL` when it names an executable bash
    or zsh, then `$SHELL` when it does, then zsh before bash (bash before zsh
    when `$SHELL` names bash), each found on PATH first and then in the usual
    directories."""
    if sys.platform == "win32" and env.get("CLAUDE_CODE_GIT_BASH_PATH"):
        return env["CLAUDE_CODE_GIT_BASH_PATH"]

    def usable(path):
        return bool(path) and os.path.isfile(path) and os.access(path, os.X_OK)

    def posix(path):
        return "bash" in path or "zsh" in path

    override = env.get("CLAUDE_CODE_SHELL", "")
    if posix(override) and usable(override):
        return override
    own = env.get("SHELL", "")
    order = ("bash", "zsh") if "bash" in own else ("zsh", "bash")
    found = [shutil.which(name, path=env.get("PATH")) for name in order]
    candidates = [
        found[0],
        *(
            f"{directory}/{name}"
            for name in order
            for directory in ("/bin", "/usr/bin", "/usr/local/bin", "/opt/homebrew/bin")
        ),
        found[1],
    ]
    if posix(own):
        candidates.insert(0, own)
    for candidate in candidates:
        if usable(candidate):
            return candidate
    raise RuntimeError("No bash or zsh on this machine for Claude Code to run")


def native_path(path):
    """This machine's own spelling of a path the session names.

    The session sees a Windows executor's paths the way Git Bash spells them
    (`/c/Users/x` for `C:\\Users\\x`, `executor_transport.session_path`),
    which Git Bash opens and nothing else here does. Elsewhere the spellings
    are the same.
    """
    if sys.platform != "win32":
        return path
    return re.sub(r"^/([A-Za-z])(?:/|$)", lambda m: m.group(1).upper() + ":/", path)


def gitignored(root, directories):
    """Those of `directories`, all under `root`, that git ignores. None when
    `root` is not a work tree or git cannot be run: nothing is ignored then."""
    if not directories:
        return set()
    names = [directory.relative_to(root).as_posix() + "/" for directory in directories]
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "check-ignore", "--stdin"],
            input="\n".join(names) + "\n",
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode not in (0, 1):
        return set()
    found = set(result.stdout.splitlines())
    return {
        directory
        for directory, name in zip(directories, names, strict=True)
        if name in found
    }


def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def skill_paths(path):
    """What a SKILL.md's frontmatter `paths` scopes it to, as written: each
    string it lists (a flow sequence as its inner text), for the session to
    split and expand as Claude Code does (`release.path_patterns`). None when
    the frontmatter has no `paths`."""
    try:
        with open(path, encoding="utf-8") as stream:
            if stream.readline().strip() != "---":
                return None
            lines = []
            for line in stream:
                line = line.rstrip("\r\n")
                if line.strip() == "---":
                    break
                lines.append(line)
            else:
                return None
    except (OSError, UnicodeError):
        return None
    for index, line in enumerate(lines):
        match = re.match(r"paths\s*:(.*)$", line)
        if not match:
            continue
        value = match.group(1).strip()
        if value.startswith("[") and value.endswith("]"):
            return [value[1:-1]]
        if value:
            return [_unquote(value)]
        items = []
        for following in lines[index + 1 :]:
            item = re.match(r"\s*-\s*(.*)$", following)
            if item:
                items.append(_unquote(item.group(1).strip()))
            elif following.strip() and not following[:1].isspace():
                break
        return items
    return None


def request(state, method, params=None):
    with connect(state) as connection:
        with connection.makefile("rwb") as stream:
            stream.write(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            stream.flush()
            line = stream.readline()
    if not line:
        raise RuntimeError(
            "Executor disconnected; request outcome must be queried by its original ID"
        )
    try:
        response = json.loads(line)
    except ValueError:
        if sys.platform != "win32":
            raise
        # An endpoint left behind by a service that died with the machine can
        # name a port something else has taken since; what answers there is
        # not an executor.
        raise ConnectionRefusedError("No executor answers at this endpoint") from None
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]
