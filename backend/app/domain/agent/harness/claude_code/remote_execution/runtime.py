"""Persistent execution service, shipped as a standalone standard-library script.

The Unix socket is reachable only by the owning user. SSH carries MCP to it;
closing an SSH connection does not discard the command registry or replay writes.

Windows has no Unix socket to put the owner's permission on, so there the
service listens on loopback TCP and the permission is a token instead: it is
written beside the port in ``executor.endpoint``, a file only the owner can
read, and a connection that does not open with it is closed. After that line
the protocol is the same one. What else differs on Windows is in portable.py.
"""

from __future__ import annotations

import base64
import contextlib
import functools
import hashlib
import hmac
import json
import os
import queue
import re
import runpy
import secrets
import select
import shlex
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from functools import partial
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


SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PROTOCOL_VERSION = 1

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
def portable():
    """The Windows primitives shipped beside this file; see portable.py."""
    return runpy.run_path(str(Path(__file__).with_name("portable.py")))


def lock(file, blocking=True):
    if sys.platform == "win32":
        portable()["lock"](file, blocking)
    else:
        fcntl.flock(file, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)


def resolve_program(argv):
    return portable()["which"](argv) if sys.platform == "win32" else argv


def socket_path(state):
    """Where a client finds this state's service: its socket, or on Windows the
    file naming its port and token. It exists only while the service may."""
    if sys.platform == "win32":
        return str(Path(state) / "executor.endpoint")
    digest = hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


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


class MCPProcess:
    def __init__(self, command, cwd, env, log):
        self.process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
            start_new_session=True,
        )
        self.lock = threading.Lock()
        self.pending = {}
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.call(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cheese-execution", "version": "0.1.0"},
            },
        )
        self.send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def send(self, value):
        with self.lock:
            assert self.process.stdin is not None
            self.process.stdin.write(json.dumps(value).encode() + b"\n")
            self.process.stdin.flush()

    def _read(self):
        try:
            assert self.process.stdout is not None
            for line in self.process.stdout:
                data = json.loads(line)
                if "method" in data:
                    if "id" in data:
                        self.send(
                            {
                                "jsonrpc": "2.0",
                                "id": data["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Executor has no client-side "
                                    "sampling or elicitation handler",
                                },
                            }
                        )
                    continue
                pending = self.pending.get(data.get("id"))
                if pending:
                    pending.put(data)
        finally:
            for pending in list(self.pending.values()):
                pending.put({"error": {"message": "MCP process disconnected"}})

    def begin(self, method, params=None):
        """Send a request; its answer lands on the returned queue."""
        with self.lock:
            self.sequence += 1
            key = self.sequence
            answers = self.pending[key] = queue.Queue()
        self.send(
            {"jsonrpc": "2.0", "id": key, "method": method, "params": params or {}}
        )
        return key, answers

    def finish(self, key, answers, timeout=300):
        """Take the answer to a request begun earlier, or raise on error."""
        try:
            data = answers.get(timeout=timeout)
        finally:
            self.pending.pop(key, None)
        if "error" in data:
            raise RuntimeError(data["error"]["message"])
        return data["result"]

    def call(self, method, params=None):
        key, answers = self.begin(method, params)
        return self.finish(key, answers)

    def close(self):
        if sys.platform == "win32":
            portable()["terminate_tree"](self.process.pid)
            self.process.wait()
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()


class Executor:
    def __init__(self, state):
        self.state = Path(state).resolve()
        # Bootstrap is reloaded each turn; successful checks belong to this process.
        self.verified_binaries = {}
        self.config = json.loads((self.state / "config.json").read_text())
        self.programs = Path(self.config.get("release", self.state.parent))
        self.admission_lock = threading.Lock()
        self.active_calls = 0
        self.upgrading = False
        upgrade = self.state / "upgrade.json"
        if upgrade.exists():
            pending = json.loads(upgrade.read_text())
            if pending["release"] == self.config.get("release") and pending.get(
                "claude", self.config.get("claude")
            ) == self.config.get("claude"):
                upgrade.unlink()
            else:
                self.upgrading = pending["ready"]
        self.root = Path(self.config["workspace"]).resolve(strict=True)
        self.env = dict(os.environ, **self.config.get("env", {}))
        # Every command this executor runs is a process of its own, in its own
        # process group, with its output in files under `commands/`: whoever
        # asked for it — the central session's shell prefix, or a caller of
        # this executor's own Bash — reads it from there, from any
        # connection, for as long as the record is kept. The processes that
        # are still ours to wait for, by command id.
        self.commands_dir = self.state / "commands"
        self.commands_dir.mkdir(exist_ok=True)
        self.running = {}
        self.command_lock = threading.RLock()
        # The shell working directory of this executor's own Bash tool, which
        # a `cd` changes for the next call, as the build's own Bash tool does.
        self.bash_cwd = self.root
        # When a watched command's record was last read, by command id.
        self.collected = {}
        self.snapshot = None
        self.snapshot_lock = threading.Lock()
        self.cli_lock = threading.Lock()
        self.clients = {}
        self.client_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.db_lock = threading.Lock()
        self.context_fs_entries = {}
        # Socket clients load this module without opening the service database.
        import sqlite3

        self.db = sqlite3.connect(
            self.state / "requests.sqlite", check_same_thread=False
        )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS requests "
            "(id TEXT PRIMARY KEY, input TEXT, result TEXT)"
        )
        self.db.commit()
        self.cli_worker = None
        self.cli_worker_ready = False
        worker = self.programs / "remote-execution/cli_worker.py"
        cli = self.programs / "cheese"
        # The worker forks and passes descriptors; Windows has neither, and
        # there `cheese` runs the CLI in its own process instead.
        if worker.is_file() and cli.is_file() and sys.platform != "win32":
            address = socket_path(self.state) + ".cli"
            Path(address).unlink(missing_ok=True)
            with (self.state / "cli-worker.log").open("a") as log:
                self.cli_worker = subprocess.Popen(
                    [sys.executable, str(worker), address, str(cli)],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    text=True,
                )
        self.log("service", "started", workspace=str(self.root), pid=os.getpid())
        threading.Thread(target=self._reap, daemon=True).start()

    def log(self, item, status, **fields):
        with self.log_lock, (self.state / "events.jsonl").open("a") as output:
            output.write(
                json.dumps({"time": stamp(), "item": item, "status": status, **fields})
                + "\n"
            )
            output.flush()

    def client(self, server):
        with self.client_lock:
            if server not in self.clients:
                if server == "native":
                    config_dir = self.state / "native-config"
                    config_dir.mkdir(exist_ok=True)
                    command = [
                        self.config["claude"],
                        "--setting-sources",
                        "",
                        "mcp",
                        "serve",
                    ]
                    # This process runs the room's file operations, and it is
                    # the build that writes the shell snapshot every command
                    # here starts from (`shell_snapshot`). So it carries the
                    # environment a command gets — the room's own, credentials
                    # included, with the platform CLI on PATH — and bash, the
                    # shell the central session wraps its commands for.
                    env = dict(
                        self.command_env(),
                        CLAUDE_CONFIG_DIR=str(config_dir),
                        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                        CLAUDE_CODE_TMPDIR=str(self.state / "serve-temp"),
                        SHELL=self.bash_program(),
                    )
                    for key in (
                        "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS",
                        "CLAUDE_CODE_SHELL_PREFIX",
                    ):
                        env.pop(key, None)
                    cwd = self.root
                else:
                    spec = self.config["mcp_servers"][server]
                    if spec.get("type", "stdio") != "stdio":
                        raise ValueError(
                            "Remote process servers require stdio transport"
                        )
                    command = [spec["command"], *spec.get("args", [])]
                    env = dict(self.env, **spec.get("env", {}))
                    cwd = spec.get("cwd", str(self.root))
                log = (
                    self.state
                    / (
                        "mcp-"
                        + hashlib.sha256(server.encode()).hexdigest()[:12]
                        + ".log"
                    )
                ).open("a")
                try:
                    self.clients[server] = MCPProcess(command, cwd, env, log)
                finally:
                    log.close()
            return self.clients[server]

    def _ready_cli_worker(self):
        if self.cli_worker is None:
            raise RuntimeError("Cheese CLI worker is unavailable")
        # A lock of its own, held only for the handshake: readiness is one line
        # read off the worker's stdout, so two threads arriving together would
        # each get half an answer.
        with self.cli_lock:
            if self.cli_worker_ready:
                return
            assert self.cli_worker.stdout is not None
            if (
                not select.select([self.cli_worker.stdout], [], [], 15)[0]
                or self.cli_worker.stdout.readline().strip() != "ready"
            ):
                self.cli_worker.kill()
                self.cli_worker.wait()
                raise RuntimeError("CLI worker startup failed; inspect cli-worker.log")
            self.env["CHEESE_CLI_SOCKET"] = socket_path(self.state) + ".cli"
            self.cli_worker_ready = True

    def invoke(self, params):
        key = params["id"]
        serialized = json.dumps(params, sort_keys=True)
        with self.db_lock:
            existing = self.db.execute(
                "SELECT input,result FROM requests WHERE id=?", (key,)
            ).fetchone()
            if existing:
                if existing[0] != serialized:
                    raise ValueError("Request ID already belongs to different input")
                if existing[1] is None:
                    raise RuntimeError(
                        "Request accepted; outcome is pending or unknown. "
                        "Do not replay with a new ID"
                    )
                return json.loads(existing[1])
            self.db.execute("INSERT INTO requests VALUES (?,?,NULL)", (key, serialized))
            self.db.commit()
        self.log(key, "started", input=params)
        try:
            result = {
                "value": self.execute(
                    params["tool"],
                    params.get("args", {}),
                    key,
                    params.get("server", "native"),
                    params.get("cwd"),
                )
            }
        except Exception as exc:
            result = {"error": str(exc)}
        with self.db_lock:
            self.db.execute(
                "UPDATE requests SET result=? WHERE id=?", (json.dumps(result), key)
            )
            self.db.commit()
        self.log(key, "failed" if "error" in result else "completed", result=result)
        return result

    def execute(self, tool, args, key, server="native", cwd=None):
        name = tool if server == "native" else f"mcp__{server}__{tool}"
        args = self.hooks("PreToolUse", name, args, key, cwd=cwd)
        result = self.execute_core(tool, args, key, server)
        self.hooks("PostToolUse", name, args, key, result, cwd=cwd)
        return result

    def hooks(self, event, tool, args, key, result=None, cwd=None):
        if self.config.get("private"):
            return args
        cwd = Path(cwd) if cwd and Path(cwd).is_dir() else self.bash_cwd
        paths = [
            self.root / ".claude/settings.json",
            self.root / ".claude/settings.local.json",
        ]
        settings = [json.loads(path.read_text()) for path in paths if path.exists()]
        settings.append(self.config.get("settings", {}))
        for source in settings:
            for group in source.get("hooks", {}).get(event, []):
                matcher = group.get("matcher", "*")
                if matcher not in ("", "*") and not re.fullmatch(matcher, tool):
                    continue
                for hook in group.get("hooks", []):
                    if hook["type"] != "command":
                        raise ValueError(
                            "Remote execution currently requires command hooks"
                        )
                    payload = {
                        "hook_event_name": event,
                        "tool_name": tool,
                        "tool_input": args,
                        "tool_use_id": key,
                        "cwd": str(cwd),
                        "session_id": self.state.name,
                    }
                    if event == "PostToolUse":
                        payload["tool_response"] = result
                    # A repository writes these hooks for plain Claude Code,
                    # which invokes them with the project root in
                    # CLAUDE_PROJECT_DIR (its documented way to reach a script
                    # the repository ships), blocks on exit 2 alone, and lets
                    # the call go ahead on any other failing exit.
                    response = subprocess.run(
                        resolve_program(["bash", "-c", hook["command"]]),
                        cwd=cwd,
                        env=dict(self.env, CLAUDE_PROJECT_DIR=str(self.root)),
                        input=json.dumps(payload),
                        capture_output=True,
                        text=True,
                        timeout=hook.get("timeout", 60),
                    )
                    if response.returncode == 2:
                        raise PermissionError(
                            f"Remote {event} hook failed: {response.stderr}"
                        )
                    if response.returncode:
                        continue
                    output = (
                        json.loads(response.stdout) if response.stdout.strip() else {}
                    )
                    specific = output.get("hookSpecificOutput", {})
                    if (
                        specific.get("permissionDecision") == "deny"
                        or output.get("decision") == "block"
                    ):
                        raise PermissionError(
                            specific.get("permissionDecisionReason")
                            or output.get("reason", "Remote hook denied operation")
                        )
                    if event == "PreToolUse" and "updatedInput" in specific:
                        args = specific["updatedInput"]
        return args

    def execute_core(self, tool, args, key, server="native"):
        if tool in self.config.get("deny_tools", []):
            raise PermissionError(f"Remote execution policy denies {tool}")
        if server != "native":
            return self.client(server).call(
                "tools/call", {"name": tool, "arguments": args}
            )
        if tool not in NATIVE_TOOLS:
            raise ValueError(f"Unsupported remote tool: {tool}")
        if tool == "Bash":
            return self.bash(args)
        if tool == "TaskStop":
            record = self.stop_command(args["task_id"])
            return {
                "message": "Remote process group stopped",
                "task_id": args["task_id"],
                "task_type": "local_bash",
                "command": record["command"],
            }
        result = self.client("native").call(
            "tools/call", {"name": tool, "arguments": args}
        )
        if result.get("isError"):
            raise RuntimeError(json.dumps(result.get("content")))
        return json.loads(result["content"][0]["text"])

    # --- commands ------------------------------------------------------------

    def bash_program(self):
        """The bash every command runs in, chosen as the build chooses it: the
        machine's own `$SHELL` when that is bash, else bash on PATH; on
        Windows, the Git Bash the build is pointed at."""
        if sys.platform == "win32" and self.env.get("CLAUDE_CODE_GIT_BASH_PATH"):
            return self.env["CLAUDE_CODE_GIT_BASH_PATH"]
        own = self.env.get("SHELL", "")
        if Path(own).name == "bash" and os.access(own, os.X_OK):
            return own
        return shutil.which("bash", path=self.env.get("PATH")) or "/bin/bash"

    def command_env(self):
        """The environment every command starts from: the room's, with the
        platform CLI first on PATH, and the build that runs here named where a
        native session names it for its commands (the snapshot's `rg` reads it)."""
        if self.cli_worker is not None:
            # Commands reach the CLI through it, so it has to be up first.
            self._ready_cli_worker()
        env = dict(
            self.env,
            PATH=str(self.programs / "remote-execution/bin")
            + os.pathsep
            + self.env.get("PATH", os.defpath),
        )
        if self.config.get("claude"):
            env["CLAUDE_CODE_EXECPATH"] = str(self.config["claude"])
        return env

    def shell_snapshot(self):
        """The snapshot of this machine's shell that the pinned build writes.

        A native Claude Code session sources a snapshot of its own machine's
        shell — functions, aliases, options, PATH — before every command. The
        central session's snapshot is of the wrong machine, so every command
        here sources this one instead: `claude mcp serve` writes it into its
        config directory the first time its Bash tool runs, from the
        environment a command here gets, so asking that tool for `true` once
        is how this executor holds exactly the file a native session on this
        machine would. None when the build could not write one; a command then
        runs without, as a native one does.
        """
        with self.snapshot_lock:
            if self.snapshot is not None and self.snapshot.exists():
                return self.snapshot
            try:
                self.client("native").call(
                    "tools/call", {"name": "Bash", "arguments": {"command": "true"}}
                )
            except Exception as exc:  # noqa: BLE001 — a command still runs
                self.log("shell-snapshot", "failed", error=str(exc))
                return None
            found = sorted(
                (self.state / "native-config/shell-snapshots").glob(
                    "snapshot-bash-*.sh"
                ),
                key=lambda path: path.stat().st_mtime,
            )
            self.snapshot = found[-1] if found else None
            return self.snapshot

    def _record(self, command_id):
        if not isinstance(command_id, str) or not COMMAND_ID.fullmatch(command_id):
            raise ValueError("Invalid command id")
        return self.commands_dir / command_id

    def start_command(
        self, command_id, argv, cwd, env, *, merge, stdin=None, watched, meta
    ):
        """Run one command as a process group of its own, output to its record.

        Its output goes to files and not through this process, so what it
        prints does not depend on anyone reading it at the time: a reader that
        loses its connection picks up at the byte it had reached.
        """
        record = self._record(command_id)
        record.mkdir()
        write_json(record / "command.json", {**meta, "watched": watched})
        out = (record / "out").open("wb")
        err = out if merge else (record / "err").open("wb")
        source = subprocess.DEVNULL
        if stdin is not None:
            (record / "in").write_bytes(stdin)
            source = (record / "in").open("rb")
        try:
            options = {
                "cwd": str(cwd),
                "env": env,
                "stdin": source,
                "stdout": out,
                "stderr": err,
            }
            if sys.platform == "win32":
                process = subprocess.Popen(
                    resolve_program(argv),
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    **options,
                )
            else:
                process = subprocess.Popen(argv, start_new_session=True, **options)
        finally:
            for stream in {out, err, source} - {subprocess.DEVNULL}:
                stream.close()
        with self.command_lock:
            self.running[command_id] = {
                "process": process,
                "read_at": time.monotonic(),
                "watched": watched,
            }
        self.log(command_id, "started", pid=process.pid, command=meta.get("command"))
        threading.Thread(
            target=self._wait_command, args=(command_id, process), daemon=True
        ).start()

    def _wait_command(self, command_id, process):
        code = process.wait()
        record = self._record(command_id)
        # Negative for a POSIX child killed by signal n, kept so its reader can
        # end the same way.
        temporary = record / ("exit." + uuid.uuid4().hex)
        temporary.write_text(str(code))
        temporary.replace(record / "exit")
        with self.command_lock:
            self.running.pop(command_id, None)
            self.collected[command_id] = time.time()
        self.log(command_id, "exited", exit_code=code)

    def _tree(self, pid):
        """The process and its descendants, parents first. A descendant that
        left the group (`setsid`) is still one: the build's own stop kills the
        tree, not the group."""
        rows = []
        if Path("/proc").is_dir():
            # /proc rather than `ps`: the private execution image has no `ps`.
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    stat = (entry / "stat").read_text()
                except OSError:
                    continue
                # The command name sits in parentheses and may hold spaces;
                # the parent pid is the second field after it.
                rows.append(
                    (int(entry.name), int(stat[stat.rindex(")") + 2 :].split()[1]))
                )
        else:
            listing = subprocess.run(
                ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True
            ).stdout
            rows = [
                (int(row[0]), int(row[1]))
                for row in (line.split() for line in listing.splitlines())
                if len(row) == 2
            ]
        children = {}
        for child, parent in rows:
            children.setdefault(parent, []).append(child)
        found, pending = [], [pid]
        while pending:
            current = pending.pop(0)
            if current not in found:
                found.append(current)
                pending.extend(children.get(current, []))
        return found

    def signal_command(self, command_id, number):
        """Send a signal to a running command's group and every descendant.

        A stop for a command not started yet is kept: the prefix's start and
        its stop travel separately, and a stop that overtook its start must
        still hold, so that start is refused (`shell`)."""
        record = self._record(command_id)
        with self.command_lock:
            entry = self.running.get(command_id)
            if entry is None and not record.exists():
                record.mkdir()
                write_json(
                    record / "command.json",
                    {"watched": True, "stopped_before_start": True, "command": ""},
                )
                (record / "exit").write_text(str(-number))
                self.log(command_id, "stopped before start", signal=number)
                return {"running": False}
        if entry is None:
            return {"running": False}
        pid = entry["process"].pid
        if sys.platform == "win32":
            portable()["terminate_tree"](pid)
            return {"running": True}
        pids = self._tree(pid)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, number)
        for member in pids:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(member, number)
        return {"running": True}

    def stop_command(self, command_id):
        """Stop a command for good: TERM, then KILL for whatever ignored it."""
        record = self._record(command_id)
        if not (record / "command.json").exists():
            raise ValueError("Unknown remote task")
        meta = json.loads((record / "command.json").read_text())
        with self.command_lock:
            entry = self.running.get(command_id)
        if entry is not None:
            # Taken once, before TERM: a descendant that ignores TERM outlives
            # the shell that parented it and is re-parented, so a second look
            # would not find it.
            pids = [] if sys.platform == "win32" else self._tree(entry["process"].pid)
            self.signal_command(command_id, signal.SIGTERM)
            time.sleep(0.3)
            for pid in pids:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(ProcessLookupError, PermissionError):
                if sys.platform != "win32":
                    os.killpg(entry["process"].pid, signal.SIGKILL)
            self.log(command_id, "stopped")
        return meta

    def read_command(self, command_id, out_offset=0, err_offset=0, wait=0.0):
        """What a command printed from the given offsets on, waiting briefly for
        more; its exit status and final directory once everything is read.

        Offsets are the reader's, so a reader that lost an answer asks again
        from where it was and gets each byte exactly once.
        """
        record = self._record(command_id)
        if not (record / "command.json").exists():
            raise ValueError("Unknown command")
        deadline = time.monotonic() + min(max(float(wait), 0.0), COMMAND_READ_WAIT_S)
        out_path, err_path = record / "out", record / "err"
        while True:
            with self.command_lock:
                entry = self.running.get(command_id)
                if entry is not None:
                    entry["read_at"] = time.monotonic()
                self.collected[command_id] = time.time()
            # The exit file first: once it exists, the process wrote all it
            # will, so the sizes read after it cover everything it printed.
            exited = (record / "exit").exists()
            out_size = out_path.stat().st_size if out_path.exists() else 0
            err_size = err_path.stat().st_size if err_path.exists() else 0
            if entry is None and not exited:
                # Not ours to wait for, and never finished: the service that
                # started it is gone.
                return {"out": "", "err": "", "lost": True}
            if (
                out_size > out_offset
                or err_size > err_offset
                or exited
                or time.monotonic() >= deadline
            ):
                break
            time.sleep(0.05)

        def chunk(path, offset, size):
            if size <= offset:
                return b""
            with path.open("rb") as stream:
                stream.seek(offset)
                return stream.read(min(size - offset, COMMAND_READ_BYTES))

        out = chunk(out_path, out_offset, out_size)
        err = chunk(err_path, err_offset, err_size)
        answer: dict = {
            "out": base64.b64encode(out).decode(),
            "err": base64.b64encode(err).decode(),
        }
        if (
            exited
            and out_offset + len(out) >= out_size
            and err_offset + len(err) >= err_size
        ):
            answer["exit"] = int((record / "exit").read_text())
            cwd = self._final_directory(record)
            if cwd is not None:
                answer["cwd"] = str(cwd)
        return answer

    def _final_directory(self, record):
        """The directory a command's shell ended in, as its `pwd -P` wrote it."""
        path = record / "cwd"
        if not path.exists():
            return None
        text = path.read_text(errors="replace")[:4096].strip()
        if sys.platform == "win32":
            # Git Bash spells C:\x as /c/x, a path only it can open.
            text = re.sub(r"^/([A-Za-z])(?=/|$)", lambda m: m.group(1) + ":", text)
        return Path(text) if text else None

    def shell(self, params):
        """The central session's shell prefix: one command, started, read and
        signalled by the id its prefix chose. Starting it again with the same
        input is answered as started, so a prefix whose answer was lost asks
        again without running it twice.

        A `control` rather than a method of its own: the route that carries
        executor calls from a session is served by the device connection's
        owner, which an app release leaves on its previous image, and that
        route admits a fixed set of methods."""
        operation = params.get("operation")
        command_id = params.get("command_id")
        record = self._record(command_id)
        if operation == "start":
            inputs = {
                key: params.get(key)
                for key in ("kind", "body", "cwd", "env", "merge", "stdin")
            }
            digest = hashlib.sha256(
                json.dumps(inputs, sort_keys=True).encode()
            ).hexdigest()
            with self.command_lock:
                if record.exists():
                    meta = json.loads((record / "command.json").read_text())
                    if meta.get("stopped_before_start"):
                        # Its stop came first: it never runs, and reads as
                        # ended by that signal.
                        return {"started": False}
                    if meta.get("digest") != digest:
                        raise ValueError(
                            "Command ID already belongs to different input"
                        )
                    return {"started": True}
                cwd = Path(params.get("cwd") or self.root)
                if not cwd.is_dir():
                    # Gone since the session last looked: its own reset.
                    cwd = self.root
                env = self.command_env()
                env.update(
                    (name, str(value))
                    for name, value in (params.get("env") or {}).items()
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                )
                if params.get("kind") == "bash":
                    snapshot = self.shell_snapshot()
                    script = (
                        (
                            f"source {shlex.quote(str(snapshot))} "
                            "2>/dev/null || true && "
                            if snapshot
                            else ""
                        )
                        + params["body"]
                        + " && pwd -P >| "
                        + shlex.quote(str(record / "cwd"))
                    )
                    argv = [self.bash_program(), "-c", script]
                elif params.get("kind") == "sh":
                    argv = (
                        [self.bash_program(), "-c", params["body"]]
                        if sys.platform == "win32"
                        else ["/bin/sh", "-c", params["body"]]
                    )
                else:
                    raise ValueError("Unknown command kind")
                stdin = params.get("stdin")
                self.start_command(
                    command_id,
                    argv,
                    cwd,
                    env,
                    merge=bool(params.get("merge")),
                    stdin=None if stdin is None else base64.b64decode(stdin),
                    watched=True,
                    meta={"digest": digest, "command": params["body"][-2000:]},
                )
            return {"started": True}
        if operation == "read":
            return self.read_command(
                command_id,
                int(params.get("out", 0)),
                int(params.get("err", 0)),
                params.get("wait", 0),
            )
        if operation == "signal":
            number = int(params["signal"])
            if number not in (
                signal.SIGTERM,
                signal.SIGINT,
                signal.SIGHUP,
                signal.SIGKILL,
            ):
                raise ValueError("Unsupported signal")
            return self.signal_command(command_id, number)
        if operation == "forget":
            with self.command_lock:
                if command_id in self.running:
                    raise ValueError("Command is still running")
                shutil.rmtree(record, ignore_errors=True)
                self.collected.pop(command_id, None)
            return {}
        raise ValueError("Unknown shell operation")

    def _reap(self):
        """Stop a watched command whose reader is gone for good, and forget a
        finished one nobody collected (`COMMAND_ABANDONED_S`)."""
        while True:
            time.sleep(30)
            now = time.monotonic()
            with self.command_lock:
                stale = [
                    command_id
                    for command_id, entry in self.running.items()
                    if entry["watched"] and now - entry["read_at"] > COMMAND_ABANDONED_S
                ]
            for command_id in stale:
                self.log(command_id, "abandoned")
                with contextlib.suppress(Exception):
                    self.stop_command(command_id)
            for record in list(self.commands_dir.iterdir()):
                try:
                    meta = json.loads((record / "command.json").read_text())
                    finished = (record / "exit").stat().st_mtime
                except (OSError, ValueError):
                    continue
                last = max(finished, self.collected.get(record.name, 0))
                if meta.get("watched") and time.time() - last > COMMAND_ABANDONED_S:
                    shutil.rmtree(record, ignore_errors=True)

    def bash(self, args):
        """This executor's own Bash tool, for the callers that have no shell of
        their own here: the Codex harness and the platform's own commands.

        It keeps the build's Bash contract those callers were written against:
        a `cd` holds for the next call and leaving the workspace returns to its
        root, a failure comes back as `Exit code N` followed by the output, a
        foreground command still running at its timeout goes on as a background
        task, and `TaskStop` stops one.
        """
        command_id = "task-" + uuid.uuid4().hex[:16]
        record = self._record(command_id)
        snapshot = self.shell_snapshot()
        script = (
            (
                f"source {shlex.quote(str(snapshot))} 2>/dev/null || true && "
                if snapshot
                else ""
            )
            + f"eval {shlex.quote(args['command'])} < /dev/null && pwd -P >| "
            + shlex.quote(str(record / "cwd"))
        )
        cwd = self.bash_cwd if self.bash_cwd.is_dir() else self.root
        self.start_command(
            command_id,
            [self.bash_program(), "-c", script],
            cwd,
            self.command_env(),
            merge=True,
            watched=False,
            meta={"command": args["command"]},
        )
        running = {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "noOutputExpected": False,
            "backgroundTaskId": command_id,
        }
        if args.get("run_in_background"):
            return running
        deadline = (
            time.monotonic() + min(max(args.get("timeout", 120000), 1), 600000) / 1000
        )
        while not (record / "exit").exists():
            if time.monotonic() >= deadline:
                return running
            time.sleep(0.05)
        code = int((record / "exit").read_text())
        if code < 0:
            # Killed by signal n: the number a shell reports.
            code = 128 - code
        final = self._final_directory(record)
        self.bash_cwd = (
            final
            if final is not None and (final == self.root or self.root in final.parents)
            else self.root
        )
        with (record / "out").open("rb") as stream:
            text = stream.read(BASH_INLINE_CHARS * 4).decode("utf-8", "replace")
        text = text[:BASH_INLINE_CHARS].rstrip("\n")
        return {
            "stdout": text if code == 0 else f"Exit code {code}\n{text}",
            "stderr": "",
            "interrupted": False,
            "noOutputExpected": False,
        }

    def control(self, params):
        kind = params["subtype"]
        if kind == "checkpoint":
            self.hooks("Stop", "", {}, params["request_id"])
            return self.invoke(
                {
                    "id": params["request_id"],
                    "tool": "Bash",
                    "args": {"command": "cheese-sync"},
                }
            )
        if kind == "shell":
            return self.shell(params)
        if kind == "read_file":
            path = Path(params["path"])
            if not path.is_absolute():
                path = self.root / path
            return {"contents": path.read_text(), "absPath": str(path)}
        if kind == "file_suggestions":
            query = params.get("query", "").casefold()
            if self.config.get("private"):
                files = [
                    p.relative_to(self.root).as_posix()
                    for p in self.root.rglob("*")
                    if p.is_file()
                    and not any(
                        part.startswith(".") for part in p.relative_to(self.root).parts
                    )
                ]
                return {"files": [p for p in files if query in p.casefold()][:100]}
            files = subprocess.check_output(
                ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
                cwd=self.root,
                text=True,
            ).splitlines()
            return {"files": [p for p in files if query in p.casefold()][:100]}
        if kind == "get_workspace_diff":
            if self.config.get("private"):
                return {"diff": ""}
            diff = subprocess.check_output(
                ["git", "diff", "HEAD", "--"], cwd=self.root, text=True
            )
            return {"diff": diff}
        raise ValueError(f"Unsupported executor control: {kind}")

    def context(self, known_files=None):
        if self.config.get("private"):
            # Never import executable configuration written by a scratch command
            # into the central Claude Code process.
            return {
                "workspace": str(self.root),
                "files": {},
                "instructions": "This chat has temporary scratch space at /work. "
                "Use shell and file tools for drafts and small processing tasks. "
                "Save finished documents through cheese_doc_set and publish artifacts "
                "through cheese show. Scratch files can disappear when execution "
                "is released; they are not permanent storage. "
                "No project checkout is available.",
            }
        files = {}
        instructions = []
        seen = set()

        def include(path):
            path = path.resolve()
            if path in seen or not path.is_file():
                return
            seen.add(path)
            text = path.read_text()
            imported = []

            def reference(match):
                candidate = Path(match.group(1)).expanduser()
                if not candidate.is_absolute():
                    candidate = path.parent / candidate
                if candidate.is_file():
                    imported.append(candidate)
                    return (
                        "[included instruction file: " + str(candidate.resolve()) + "]"
                    )
                return match.group()

            text = re.sub(r"(?<![\w`])@([^\s`]+)", reference, text)
            instructions.append("# " + str(path) + "\n\n" + text)
            for candidate in imported:
                include(candidate)

        paths = [self.root / "CLAUDE.md", self.root / "CLAUDE.local.md"]
        for path in paths:
            include(path)
        for directory in (self.root / ".claude",):
            if directory.exists():
                paths.extend(p for p in directory.rglob("*") if p.is_file())
        file_names = []
        for path in paths:
            if path.is_file():
                name = path.relative_to(self.root).as_posix()
                file_names.append(name)
                content = path.read_bytes()
                if (known_files or {}).get(name) != hashlib.sha256(content).hexdigest():
                    files[name] = base64.b64encode(content).decode()
        return {
            "workspace": str(self.root),
            "files": files,
            "file_names": file_names,
            "instructions": "\n\n".join(instructions),
        }

    def task_fs(self, params):
        """Read or edit one task's live worktree, never the room's host filesystem."""
        task_id = str(uuid.UUID(params["task_id"]))
        home = Path(self.env["HOME"]).resolve()
        root = (home / ".cheese" / "tasks" / task_id).resolve()
        root.relative_to(home)
        if not root.is_dir():
            return {"error": "not_found"}
        operation = params["operation"]
        if operation == "diff":

            def git(*args, expected=(0,)):
                result = subprocess.run(
                    ["git", "-C", str(root), *args], capture_output=True, timeout=30
                )
                if result.returncode not in expected:
                    raise ValueError("Cannot read the task's Git comparison")
                return result.stdout

            base = "refs/remotes/origin/" + params["base_branch"]
            ancestor = git("merge-base", "HEAD", base).decode().strip()
            diff = git("diff", "--no-ext-diff", "--no-textconv", ancestor, "--")
            untracked = git("ls-files", "--others", "--exclude-standard", "-z")
            for name in untracked.decode().split("\0"):
                if not name:
                    continue
                path = (root / name).resolve()
                try:
                    path.relative_to(root)
                except ValueError:
                    continue
                if path.is_file():
                    diff += git(
                        "diff",
                        "--no-index",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--",
                        "/dev/null",
                        name,
                        expected=(0, 1),
                    )
            return {"diff": diff.decode("utf-8", errors="replace")}
        if operation == "tree":
            listed = (
                subprocess.run(
                    [
                        "git",
                        "-C",
                        str(root),
                        "ls-files",
                        "-c",
                        "-o",
                        "--exclude-standard",
                        "-z",
                    ],
                    capture_output=True,
                    check=True,
                )
                .stdout.decode()
                .split("\0")
            )
            files = []
            for name in sorted(set(listed) - {""}):
                path = root / name
                try:
                    path.resolve().relative_to(root)
                    if path.is_file():
                        files.append({"path": name, "bytes": path.stat().st_size})
                except (ValueError, OSError):
                    continue
            return {"files": files}
        name = params.get("path", "")
        relative = Path(name)
        if (
            not name
            or relative.is_absolute()
            or any(p in ("..", ".git") for p in relative.parts)
        ):
            raise ValueError("Invalid task file path")
        path = (root / relative).resolve()
        path.relative_to(root)
        if not path.is_file():
            return {"error": "not_found"}
        if operation == "read":
            offset = params.get("offset", 0)
            count = params.get("size", 1024 * 1024)
            if (
                not isinstance(offset, int)
                or not isinstance(count, int)
                or offset < 0
                or not 0 <= count <= 2 * 1024 * 1024
            ):
                raise ValueError("Invalid task file byte range")
            with path.open("rb") as file:
                before = os.fstat(file.fileno())
                version = hashlib.file_digest(file, "sha256").hexdigest()[:16]
                if params.get("version") and params["version"] != version:
                    return {"error": "conflict"}
                file.seek(offset)
                data = file.read(count)
                after = os.fstat(file.fileno())
                if (before.st_size, before.st_mtime_ns) != (
                    after.st_size,
                    after.st_mtime_ns,
                ):
                    return {"error": "conflict"}
            return {
                "data": base64.b64encode(data).decode(),
                "bytes": before.st_size,
                "version": version,
            }
        if operation in ("write", "write_bytes"):
            limit = (1 if operation == "write" else 10) * 1024 * 1024
            if path.stat().st_size > limit:
                raise ValueError("Text file is too large")
            old = path.read_bytes()
            version = hashlib.sha256(old).hexdigest()[:16]
            if params.get("version") != version:
                return {"error": "conflict"}
            if operation == "write" and b"\0" in old[:8192]:
                raise ValueError("Binary task files cannot be edited as text")
            if operation == "write":
                old.decode("utf-8")
                data = params["content"].encode("utf-8")
            else:
                data = base64.b64decode(params["data"], validate=True)
            if len(data) > limit:
                raise ValueError("Text file is too large")
            temporary = path.with_name(path.name + ".cheese-save-" + uuid.uuid4().hex)
            try:
                temporary.write_bytes(data)
                temporary.chmod(path.stat().st_mode & 0o777)
                if path.read_bytes() != old:
                    return {"error": "conflict"}
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
            return {"version": hashlib.sha256(data).hexdigest()[:16]}
        raise ValueError("Unknown task filesystem operation")

    def repo_search(self, params):
        """Find lines in this room's checkout carrying ALL the given keywords.

        What it is for: 记忆只记 repo 里查不到的东西（结论 61），and the only
        place that can answer "is it in the repo" is the machine holding the
        checkout — the platform has no copy of it. Tracked files and untracked
        ones both count: a fact written in a file that is not committed yet is
        still written down somewhere a person will read.

        ``--and`` between the keywords, not one ``-e`` each: OR is how this
        fails in exactly the repo it matters in. 「前端构建用 pnpm，不要用 npm」
        carries the keyword ``npm``, which in a real frontend repo is in the
        lockfile, every `package.json`, CI and half of `docs/` — the caller's
        window fills up in path order and the one line that actually states the
        fact never arrives. Which keywords to AND is the platform's call; this
        answers the question it was handed.

        ``searched`` is the first field, not a convenience: 「repo 里确实没写」
        and 「这次没查成」 are opposite answers. A caller that cannot tell them
        apart keeps accepting writes on a machine where this never runs, with
        nothing anywhere saying so. A private chat rents no place and has no
        checkout (结论 19), so it says that rather than searching whatever the
        process happens to be standing in.
        """
        if self.config.get("private"):
            return {"searched": False, "reason": "no-checkout", "hits": []}
        terms = [
            term
            for term in params.get("terms", [])
            if isinstance(term, str) and term.strip()
        ][:12]  # a bound on argv, not the choice of keywords
        if not terms:
            return {"searched": False, "reason": "no-terms", "hits": []}
        argv = [
            "git",
            "-C",
            str(self.root),
            "grep",
            "--no-color",
            "-n",  # line numbers: the refusal names a place, not just a file
            "-I",  # never a binary
            "-F",  # the keywords are literals, not patterns
            "-i",
            "--untracked",
        ]
        for index, term in enumerate(terms):
            argv += (["--and"] if index else []) + ["-e", term]
        try:
            result = subprocess.run(argv, capture_output=True, timeout=20)
        except (OSError, subprocess.SubprocessError) as exc:
            return {"searched": False, "reason": f"did not run: {exc}", "hits": []}
        # 1 = 一行都没匹配上，那是一个答案。其余都是没查成：这个仓库不是仓库、
        # 这台机器上没有 git、这个 git 不认某个选项。说出来——「从此一条都拦不住
        # 而日志里一个字都没有」是这套东西最坏的坏法，因为它看起来跟一切正常一样。
        if result.returncode not in (0, 1):
            stderr = result.stderr.decode("utf-8", "replace").strip()[:200]
            return {
                "searched": False,
                "reason": f"exit {result.returncode}: {stderr}",
                "hits": [],
            }
        hits = []
        for line in result.stdout.decode("utf-8", "replace").splitlines()[:200]:
            path, _, rest = line.partition(":")
            number, _, text = rest.partition(":")
            if not number.isdigit():
                continue
            hits.append({"path": path, "line": int(number), "text": text[:400]})
        return {"searched": True, "hits": hits}

    def context_fs(self, params):
        """Expose project context, with writes limited to project workflows."""
        if self.config.get("private"):
            if params.get("operation", "tree") != "tree":
                raise ValueError("Private project context is not writable")
            return {"generation": hashlib.sha256(b"private").hexdigest(), "entries": {}}

        root = self.root.resolve()
        operation = params.get("operation", "tree")
        if operation == "statfs" and sys.platform == "win32":
            usage = shutil.disk_usage(root)
            return {
                "f_bsize": 1,
                "f_blocks": usage.total,
                "f_bfree": usage.free,
                "f_bavail": usage.free,
            }
        if operation == "statfs":
            fs = os.statvfs(root)
            return {
                "f_bsize": fs.f_bsize,
                "f_blocks": fs.f_blocks,
                "f_bfree": fs.f_bfree,
                "f_bavail": fs.f_bavail,
            }
        if operation in ("directory", "list"):
            # The project's directories, looked up one at a time: the central
            # session keeps its shell's directory only if it exists in the
            # forwarded view, and a directory a command just made exists only
            # here. Directories only — files stay with the file tools.
            relative = Path(params.get("path", ""))
            if relative.is_absolute() or ".." in relative.parts:
                return {"missing": True}
            try:
                path = (root / relative).resolve(strict=True)
                path.relative_to(root)
            except (OSError, ValueError):
                return {"missing": True}
            if not path.is_dir():
                return {"missing": True}
            if operation == "list":
                names = []
                for child in path.iterdir():
                    with contextlib.suppress(OSError, ValueError):
                        child.resolve(strict=True).relative_to(root)
                        if child.is_dir():
                            names.append(child.name)
                return {"directories": sorted(names)}
            stat = path.stat()
            return {
                "mode": stat.st_mode & 0o777,
                "mtime_ns": stat.st_mtime_ns,
                "nlink": stat.st_nlink,
            }
        if operation in {
            "mkdir",
            "create",
            "write",
            "truncate",
            "rename",
            "unlink",
            "rmdir",
        }:

            def workflow_path(name):
                relative = Path(name)
                parts = relative.parts
                claude_directory = operation == "mkdir" and parts == (".claude",)
                if (
                    relative.is_absolute()
                    or not (
                        claude_directory
                        or len(parts) >= 2
                        and parts[:2] == (".claude", "workflows")
                    )
                    or (len(parts) == 2 and operation not in {"mkdir", "rmdir"})
                    or ".." in parts
                    or any(
                        (root.joinpath(*parts[:index])).is_symlink()
                        for index in range(1, len(parts) + 1)
                    )
                ):
                    raise ValueError("Only project workflows may be changed")
                path = root / relative
                path.resolve().relative_to(root)
                return path

            path = workflow_path(params.get("path", ""))
            if operation == "mkdir":
                path.mkdir(mode=0o700)
            elif operation == "create":
                fd = os.open(
                    path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW, 0o600
                )
                os.close(fd)
            elif operation in {"write", "truncate"}:
                fd = os.open(path, os.O_WRONLY | NOFOLLOW | BINARY)
                try:
                    if operation == "write":
                        data = base64.b64decode(params["data"], validate=True)
                        offset = params["offset"]
                        if (
                            not isinstance(offset, int)
                            or offset < 0
                            or offset + len(data) > 10 * 1024 * 1024
                        ):
                            raise ValueError("Invalid workflow write range")
                        written = 0
                        while written < len(data):
                            written += pwrite(fd, data[written:], offset + written)
                    else:
                        size = params["size"]
                        if (
                            not isinstance(size, int)
                            or not 0 <= size <= 10 * 1024 * 1024
                        ):
                            raise ValueError("Invalid workflow size")
                        os.ftruncate(fd, size)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                if operation == "write":
                    return {"written": written}
            elif operation == "rename":
                destination = workflow_path(params["destination"])
                os.replace(path, destination)
            elif operation == "unlink":
                path.unlink()
            else:
                path.rmdir()
            return {"ok": True}
        if params.get("operation") == "read":
            name = params.get("path", "")
            entry = self.context_fs_entries.get(name)
            if not entry or entry["kind"] != "file":
                raise FileNotFoundError(name)
            offset = params.get("offset", 0)
            size = params.get("size", 0)
            if (
                not isinstance(offset, int)
                or not isinstance(size, int)
                or offset < 0
                or size < 0
            ):
                raise ValueError("Invalid context filesystem byte range")
            path = root / name
            try:
                path.resolve().relative_to(root)
            except (OSError, ValueError) as exc:
                raise FileNotFoundError(name) from exc
            with path.open("rb") as stream:
                stream.seek(offset)
                content = stream.read(size)
            return {"data": base64.b64encode(content).decode()}

        selected = set()
        unsupported_imports = set()
        unsupported_paths = set()

        def project_path(path):
            try:
                path.resolve().relative_to(root)
            except (OSError, ValueError):
                return None
            return path

        def include(path, *, imports=False, depth=0):
            candidate = project_path(path)
            if candidate is None:
                if path.is_symlink():
                    try:
                        unsupported_paths.add(path.relative_to(root).as_posix())
                    except ValueError:
                        pass
                return
            path = candidate
            relative = path.relative_to(root)
            if relative in (
                Path(".claude/settings.json"),
                Path(".claude/settings.local.json"),
            ):
                return
            if path in selected or not path.exists():
                return
            selected.add(path)
            if path.is_symlink():
                target = project_path(path.resolve())
                if target is None:
                    unsupported_paths.add(path.relative_to(root).as_posix())
                elif target != path:
                    include(target, imports=imports, depth=depth)
                return
            if path.is_dir():
                for child in path.iterdir():
                    include(child, imports=imports, depth=depth)
                return
            if not imports or path.suffix != ".md" or depth >= 5:
                return
            try:
                text = path.read_text()
            except (OSError, UnicodeError):
                return
            for match in re.finditer(r"(?<![\w`])@([^\s`]+)", text):
                reference = Path(match.group(1)).expanduser()
                if reference.is_absolute():
                    unsupported_imports.add(str(reference))
                    continue
                candidate = project_path(path.parent / reference)
                if candidate is None:
                    unsupported_imports.add(match.group(1))
                elif candidate.exists():
                    include(candidate, imports=True, depth=depth + 1)

        for path in (
            root / "CLAUDE.md",
            root / "CLAUDE.local.md",
            root / ".claude/rules",
            root / ".claude/commands",
            root / ".claude/agents",
        ):
            include(path, imports=True)
        for name in ("CLAUDE.md", "CLAUDE.local.md"):
            for path in root.rglob(name):
                if ".git" not in path.relative_to(root).parts:
                    include(path, imports=True)
        if (root / ".claude").is_dir() and not (root / ".claude").is_symlink():
            selected.add(root / ".claude")
        include(root / ".claude/skills")
        include(root / ".claude/workflows")

        entries = {}
        for path in selected:
            relative = path.relative_to(root).as_posix()
            stat = path.lstat()
            if path.is_symlink():
                kind = "symlink"
            elif path.is_dir():
                kind = "directory"
            elif path.is_file():
                kind = "file"
            else:
                continue
            entries[relative] = {
                "kind": kind,
                "mode": stat.st_mode & 0o777,
                "mtime_ns": stat.st_mtime_ns,
                "size": stat.st_size,
                "nlink": stat.st_nlink,
            }
            if kind == "symlink":
                entries[relative]["target"] = os.readlink(path)
            parent = path.parent
            while parent != root:
                name = parent.relative_to(root).as_posix()
                if name not in entries:
                    parent_stat = parent.lstat()
                    entries[name] = {
                        "kind": "directory",
                        "mode": parent_stat.st_mode & 0o777,
                        "mtime_ns": parent_stat.st_mtime_ns,
                        "size": parent_stat.st_size,
                        "nlink": parent_stat.st_nlink,
                    }
                parent = parent.parent

        generation = hashlib.sha256(
            json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if params.get("operation", "tree") == "tree":
            self.context_fs_entries = entries
            return {
                "generation": generation,
                "entries": entries,
                "unsupported_imports": sorted(unsupported_imports),
                "unsupported_paths": sorted(unsupported_paths),
                "hooks": self.project_tool_hooks(),
            }
        raise ValueError("Unsupported context filesystem operation")

    def project_tool_hooks(self):
        """The project's own PreToolUse and PostToolUse command hooks.

        The central session registers them, so the build fires them for the
        calls it runs itself — Bash above all — with its own input, and their
        commands run here through its shell prefix. The file tools the plugin
        runs here keep their hooks in `hooks`. A hook that is not a shell
        command string is left out: nothing of the project runs on the
        session host.
        """
        hooks = {}
        for name in ("settings.json", "settings.local.json"):
            path = self.root / ".claude" / name
            try:
                path.resolve(strict=True).relative_to(self.root)
                settings = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            for event in ("PreToolUse", "PostToolUse"):
                for group in (settings.get("hooks") or {}).get(event) or []:
                    commands = [
                        {
                            key: hook[key]
                            for key in ("type", "command", "timeout")
                            if key in hook
                        }
                        for hook in group.get("hooks") or []
                        if hook.get("type") == "command"
                        and isinstance(hook.get("command"), str)
                        and "args" not in hook
                    ]
                    if commands:
                        hooks.setdefault(event, []).append(
                            {
                                **(
                                    {"matcher": group["matcher"]}
                                    if isinstance(group.get("matcher"), str)
                                    else {}
                                ),
                                "hooks": commands,
                            }
                        )
        return hooks

    def prepare(self, payload):
        owner = self.state.parents[5]
        home = (
            owner
            / ".cheese/home"
            / str(uuid.UUID(payload["project"]))
            / str(uuid.UUID(payload["resource"]))
        )
        if home / ".cheese/executor" != self.state:
            raise ValueError("Executor preparation belongs to another room")
        bootstrap = runpy.run_path(str(self.programs / "remote-execution/bootstrap.py"))
        with bootstrap["prepared"](payload, owner, self.verified_binaries) as (
            _,
            config,
            _,
            _,
        ):
            if {k: v for k, v in self.config.items() if k != "env"} != {
                k: v for k, v in config.items() if k != "env"
            }:
                raise RuntimeError(
                    "Executor configuration changed; restart the room environment"
                )
            info = {
                **self.dispatch("configure", {"env": config["env"]}),
                "mcp_servers": list(config["mcp_servers"]),
                "context_tree": self.context_fs({"operation": "tree"}),
            }
            if payload.get("environment"):
                runner = runpy.run_path(str(self.programs / "cheese-environment.py"))
                info["environment_status"] = runner["read_status"](
                    home / ".cheese-environment"
                )["state"]
            return info

    def dispatch(self, method, params):
        if method == "begin_upgrade":
            with self.admission_lock:
                busy = not self.upgrading and (
                    self.active_calls > 0 or bool(self.running)
                )
                result = {
                    "release": params["release"],
                    "claude": params.get("claude", self.config.get("claude")),
                    "ready": not busy,
                }
                write_json(self.state / "upgrade.json", result)
                if not busy:
                    self.upgrading = True
                return result
        if method == "ping":
            return self._dispatch(method, params)
        # Reaching a command that already exists is not new work: its reader
        # has to be able to finish collecting it while the service drains.
        inspect_task = (
            method == "control"
            and params.get("subtype") == "shell"
            and params.get("operation") in {"read", "signal", "forget"}
        ) or (method == "invoke" and params.get("tool") == "TaskStop")
        with self.admission_lock:
            if self.upgrading and not inspect_task:
                raise RuntimeError("Executor is upgrading; request was not accepted")
            self.active_calls += 1
        try:
            return self._dispatch(method, params)
        finally:
            with self.admission_lock:
                self.active_calls -= 1

    def _dispatch(self, method, params):
        if method == "prepare":
            return self.prepare(params)
        if method == "configure" or (
            method == "configure_private" and self.config.get("private")
        ):
            self.env.update(params["env"])
            self.config["env"] = params["env"]
            write_json(self.state / "config.json", self.config)
            return {
                "pid": os.getpid(),
                "workspace": str(self.root),
                "state": str(self.state),
            }
        if method == "ping":
            manifest = self.programs / "executor-files.json"
            files = {}
            if manifest.exists():
                for name in json.loads(manifest.read_text()):
                    path = self.programs / name
                    if path.is_file():
                        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "pid": os.getpid(),
                "workspace": str(self.root),
                "files": files,
                "runtime_sha256": SOURCE_SHA256,
                "protocol_version": PROTOCOL_VERSION,
                "release": self.config.get("release"),
                "upgrading": self.upgrading,
                "capabilities": ["prepare", "idle_upgrade"]
                + (
                    ["cli_worker"]
                    if self.cli_worker is not None and self.cli_worker.poll() is None
                    else []
                ),
            }
        if method == "invoke":
            return self.invoke(params)
        if method == "control":
            return self.control(params)
        if method == "context":
            return self.context(params.get("known_files"))
        if method == "context_fs":
            return self.context_fs(params)
        if method == "task_fs":
            return self.task_fs(params)
        if method == "repo_search":
            return self.repo_search(params)
        if method == "mcp":
            return self.client(params["server"]).call(
                params["method"], params.get("params")
            )
        raise ValueError("Unknown executor method")

    def close(self):
        for command_id in list(self.running):
            self.stop_command(command_id)
        for client in self.clients.values():
            client.close()
        if self.cli_worker is not None:
            assert self.cli_worker.stdin is not None
            self.cli_worker.stdin.close()
            self.cli_worker.wait(timeout=10)


def serve(state):
    lock_file = (state / "service.lock").open("a")
    lock(lock_file, blocking=False)
    executor = Executor(state)
    token = secrets.token_hex(32)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            if sys.platform == "win32" and not hmac.compare_digest(
                self.rfile.readline(256).rstrip(b"\r\n"), token.encode()
            ):
                return
            try:
                value = json.loads(self.rfile.readline())
                if sys.platform == "win32" and value["method"] == "shutdown":
                    # What SIGTERM is on POSIX: Windows can only kill a process
                    # outright, and this one has children to stop first.
                    stop(None, None)
                    response = {"result": {}}
                else:
                    response = {
                        "result": executor.dispatch(
                            value["method"], value.get("params", {})
                        )
                    }
            except Exception as exc:
                response = {"error": str(exc)}
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(json.dumps(response).encode() + b"\n")

    if sys.platform == "win32":

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = False

        address = ("127.0.0.1", 0)
    else:

        class Server(socketserver.ThreadingUnixStreamServer):
            daemon_threads = False

        address = socket_path(state)

    path = Path(socket_path(state))
    path.unlink(missing_ok=True)
    with Server(address, Handler) as server:
        if sys.platform == "win32":
            # Written only once the port is listening, so a client that finds
            # the file finds a service behind it.
            write_json(path, {"port": server.server_address[1], "token": token})
        else:
            path.chmod(0o600)

        def stop(_signum, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            executor.close()
            path.unlink(missing_ok=True)
    # server_close joins request handlers before their receipt database closes.
    executor.db.close()


def bridge(state, server, *, call=None):
    if call is None:
        call = partial(request, state)

    output_lock = threading.Lock()
    bridge_id = uuid.uuid4().hex

    def handle(data):
        if "id" not in data:
            return
        try:
            method = data["method"]
            params = data.get("params", {})
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "cheese-execution", "version": "0.1.0"},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/call":
                value = call(
                    "invoke",
                    {
                        "id": f"mcp-{bridge_id}-{data['id']}",
                        "server": server,
                        "tool": params["name"],
                        "args": params.get("arguments", {}),
                    },
                )
                result = value.get("value") or {
                    "content": [{"type": "text", "text": value["error"]}],
                    "isError": True,
                }
            else:
                result = call(
                    "mcp", {"server": server, "method": method, "params": params}
                )
            response = {"jsonrpc": "2.0", "id": data["id"], "result": result}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": data["id"],
                "error": {"code": -32000, "message": str(exc)},
            }
        with output_lock:
            print(json.dumps(response), flush=True)

    threads = []
    for line in sys.stdin:
        thread = threading.Thread(target=handle, args=(json.loads(line),))
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["start", "serve", "bridge", "request", "stop"])
    parser.add_argument("--state", required=True, type=Path)
    parser.add_argument("--server", default="native")
    args = parser.parse_args()
    state = args.state.resolve()
    if args.mode == "serve":
        serve(state)
    elif args.mode == "bridge":
        bridge(state, args.server)
    elif args.mode == "request":
        value = json.load(sys.stdin)
        print(json.dumps(request(state, value["method"], value.get("params"))))
    elif args.mode == "stop":
        try:
            info = request(state, "ping")
        except (FileNotFoundError, ConnectionRefusedError):
            if not state.exists():
                return
            info = None
        if info is not None:
            if sys.platform == "win32":
                request(state, "shutdown")
            else:
                os.kill(info["pid"], signal.SIGTERM)
        with (state / "service.lock").open("a") as lock_file:
            for _ in range(100):
                try:
                    # The socket closes before handlers finish; the lock marks shutdown.
                    lock(lock_file, blocking=False)
                    return
                except BlockingIOError:
                    time.sleep(0.1)
        raise RuntimeError("Executor did not stop")
    else:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        configuration = json.load(sys.stdin)
        start_lock = (state / "start.lock").open("a")
        with start_lock:
            lock(start_lock)
            if (state / "config.json").exists():
                if json.loads((state / "config.json").read_text()) != configuration:
                    raise ValueError(
                        "Session configuration changed; use a new state directory"
                    )
                try:
                    print(json.dumps(request(state, "ping")))
                    return
                except (OSError, RuntimeError):
                    pass
            write_json(state / "config.json", configuration)
            argv = [
                sys.executable,
                str(Path(__file__).resolve()),
                "serve",
                "--state",
                str(state),
            ]
            with (state / "service.log").open("a") as log:
                options = {"stdin": subprocess.DEVNULL, "stdout": log, "stderr": log}
                process = (
                    portable()["popen_daemon"](argv, **options)
                    if sys.platform == "win32"
                    else subprocess.Popen(argv, start_new_session=True, **options)
                )
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Executor startup failed; inspect service.log")
                try:
                    print(json.dumps(request(state, "ping")))
                    return
                except (OSError, RuntimeError):
                    time.sleep(0.05)
            raise RuntimeError("Executor readiness timed out")


if __name__ == "__main__":
    main()
