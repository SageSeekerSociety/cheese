"""Persistent execution service, shipped as a standalone standard-library script.

The Unix socket is reachable only by the owning user. SSH carries MCP to it;
closing an SSH connection does not discard the command registry or replay writes.

Windows has no Unix socket to put the owner's permission on, so there the
service listens on loopback TCP and the permission is a token instead: it is
written beside the port in ``executor.endpoint``, a file only the owner can
read, and a connection that does not open with it is closed. After that line
the protocol is the same one. What else differs on Windows is in portable.py.

The bodies of everything below moved out of this file on 2026-10-16, into the
four modules beside it that it imports: `runtime_core` (the platform
primitives, the constants, where a state's service is reached and the one call
that reaches it), `runtime_commands` (the command registry), `runtime_context`
(the project's context and the task filesystem) and `runtime_calls` (the tool
calls). What stays here is the class, with one-line forwarders to those bodies
— the names a test patches on this module (`time`, `COMMAND_ABANDONED_S`,
`request`, `fcntl`) are read from here, where they are — and the three entry
points: `serve`, `bridge` and `main`. Every name this file used to hold is
re-exported, so nothing that reaches it by this path changed.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import secrets
import shutil
import signal
import socket
import socketserver
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

if sys.platform != "win32":
    # Re-exported from `runtime_core`, where the lock that takes it lives: this
    # is the name the stop test's observer patches (`runtime.fcntl.flock`), and
    # it is the module itself, so the patch reaches the caller either way.
    import fcntl  # noqa: F401

# The pieces shipped beside this file (`RELEASE_FILES`): the primitives and
# helpers all of them read, the command registry, the project's context and
# the tool calls themselves.
if __package__:
    from . import runtime_calls as calls
    from . import runtime_commands as commands
    from . import runtime_context as context
    from . import runtime_core as core
else:
    # Its siblings sit beside it, which a script run has first on `sys.path`
    # and a file loaded by path — `bootstrap` installs one and `predecessor`
    # loads the release's — has not.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_calls as calls
    import runtime_commands as commands
    import runtime_context as context
    import runtime_core as core

# This file's own bytes, and the protocol it answers with: the two names that
# are about the release rather than about how it is used.
SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PROTOCOL_VERSION = 1

# Everything this file held before its bodies moved out, taken from the module
# that holds it now and bound here as this module's own global: a caller or a
# test that reaches `runtime.NAME` still resolves, and the code above reads it
# here — which is how `_reap` is still the one patched name's reader.
_OWN = core._OWN
RELEASE_FILES = core.RELEASE_FILES
# The `control` operations that reach a command rather than make one: the one
# thing the facade reads of `runtime_commands` at call time, so an executor
# that is draining still answers them.
CONTROL_INSPECT = commands.SHELL_INSPECT
NATIVE_TOOLS = core.NATIVE_TOOLS
COMMAND_ABANDONED_S = core.COMMAND_ABANDONED_S
# How much longer than the sleep it asked for a wake-up may take before this
# executor believes it was stopped meanwhile — a frozen sandbox's processes do
# not run, so the freezer's sleep and every clock this executor reads while it
# is stopped (``time.monotonic`` included) jump forward by however long it
# slept. Four times the sleep plus this: ordinary wake-up lateness stays well
# inside it, a freeze of seconds does not.
THAW_JUMP = 2.0
OUTPUT_KEPT_S = core.OUTPUT_KEPT_S
EXECUTOR_IDLE_EXIT_S = core.EXECUTOR_IDLE_EXIT_S
EXECUTOR_IDLE_CHECK_S = core.EXECUTOR_IDLE_CHECK_S
STOP_SIGNALS = core.STOP_SIGNALS
COMMAND_READ_WAIT_S = core.COMMAND_READ_WAIT_S
COMMAND_READ_BYTES = core.COMMAND_READ_BYTES
BASH_INLINE_CHARS = core.BASH_INLINE_CHARS
COMMAND_ID = core.COMMAND_ID
NOFOLLOW = core.NOFOLLOW
BINARY = core.BINARY
pwrite = core.pwrite
stamp = core.stamp
write_json = core.write_json
beside = core.beside
project_hooks = core.project_hooks
lock = core.lock
resolve_program = core.resolve_program
socket_path = core.socket_path
socket_directory = core.socket_directory
platform_socket_directory = core.platform_socket_directory
connect = core.connect
claude_shell = core.claude_shell
native_path = core.native_path
gitignored = core.gitignored
_unquote = core._unquote
skill_paths = core.skill_paths
request = core.request


def _pipe_socket(src, dst):
    try:
        while True:
            chunk = src.recv(65536)
            if not chunk:
                break
            dst.sendall(chunk)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            dst.shutdown(socket.SHUT_WR)


def _start_forge_bridge(forge_sock: Path):
    """Loopback TCP listener inside the sandbox that pipes to `forge.sock`."""

    class _Handler(socketserver.BaseRequestHandler):
        def handle(self):
            try:
                upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                upstream.connect(str(forge_sock))
            except OSError:
                return
            with upstream:
                t1 = threading.Thread(
                    target=_pipe_socket, args=(self.request, upstream), daemon=True
                )
                t2 = threading.Thread(
                    target=_pipe_socket, args=(upstream, self.request), daemon=True
                )
                t1.start()
                t2.start()
                t1.join()
                t2.join()

    class _Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    server = _Server(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class Executor:
    def __init__(self, state):
        self.state = Path(state).resolve()
        self.config = json.loads((self.state / "config.json").read_text())
        self.programs = Path(self.config.get("release", self.state.parent))
        self.admission_lock = threading.Lock()
        self.active_calls, self.upgrading = 0, False
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
        self.forge_bridge = None
        if sys.platform != "win32":
            platform_dir = Path(platform_socket_directory(self.state))
            forge_sock = platform_dir / "forge.sock"
            ca_bundle = platform_dir / "ca-bundle.pem"
            ca_pem = platform_dir / "ca.pem"
            if forge_sock.exists() and ca_bundle.is_file():
                self.forge_bridge = _start_forge_bridge(forge_sock)
                proxy_url = f"http://127.0.0.1:{self.forge_bridge.server_address[1]}"
                # Only names that can never be a forge: the site's name goes
                # through the proxy too, which dials it over the machine's
                # forward (`forge_proxy.ForgeProxyRuntime.dial`), since a
                # project's forge may live on it and only the proxy holds the
                # forge's credential.
                no_proxy = "127.0.0.1,localhost,127.0.0.2,platform.cheese"
                self.env.update(
                    HTTPS_PROXY=proxy_url,
                    https_proxy=proxy_url,
                    NO_PROXY=no_proxy,
                    no_proxy=no_proxy,
                    SSL_CERT_FILE=str(ca_bundle),
                    REQUESTS_CA_BUNDLE=str(ca_bundle),
                    GIT_SSL_CAINFO=str(ca_bundle),
                    CURL_CA_BUNDLE=str(ca_bundle),
                    NODE_EXTRA_CA_CERTS=str(ca_pem),
                )
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
        # The process groups its commands started and that still have a
        # process in them, and when it last had a request or a command end:
        # what it ends, and when, once it has sat idle (`idle_stop`).
        self.groups = set()
        self.active_at = time.monotonic()
        self.stopping = False
        # Foreground Bash calls still waiting on their command (call → command),
        # and the ones told to stop waiting (`control` "background").
        self.foreground = {}
        self.backgrounded = set()
        # The shell working directory of this executor's own Bash tool, which
        # a `cd` changes for the next call, as the build's own Bash tool does.
        self.bash_cwd = self.root
        # When a watched command's record was last read, by command id.
        self.collected, self.snapshot = {}, None
        self.snapshot_lock, self.cli_lock = threading.Lock(), threading.Lock()
        self.clients = {}
        # Commands added in project settings and agent types' own, by name:
        # their definitions come with the calls.
        self.handed_specs = {}
        # What the session's gateway hands with a local server's calls
        # (`RemoteClient._local_start`): the values of the project secrets its
        # definition names, and — on a member's computer — the fingerprints of
        # the commands its owner lets run here. Read when the server starts.
        self.server_values = {}
        self.server_allowed = {}
        self.client_lock, self.log_lock = threading.Lock(), threading.Lock()
        self.db_lock, self.context_fs_entries = threading.Lock(), {}
        # Socket clients load this module without opening the service database.
        import sqlite3  # deferred-import: socket clients load this without a DB

        self.db = sqlite3.connect(
            self.state / "requests.sqlite", check_same_thread=False
        )
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS requests "
            "(id TEXT PRIMARY KEY, input TEXT, result TEXT)"
        )
        self.db.commit()
        self.cli_worker, self.cli_worker_ready = None, False
        worker = self.programs / "remote-execution/cli_worker.py"
        cli = self.programs / "cheese"
        # The worker forks and passes descriptors; Windows has neither, and
        # there `cheese` runs the CLI in its own process instead.
        if worker.is_file() and cli.is_file() and sys.platform != "win32":
            address = str(Path(socket_directory(self.state)) / "cli.sock")
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

    # --- the calls -----------------------------------------------------------

    def client(self, server):
        return calls.client(self, server)

    def _ready_cli_worker(self):
        return calls._ready_cli_worker(self)

    def invoke(self, params):
        return calls.invoke(self, params)

    def execute(self, tool, args, key, server="native", cwd=None, tool_hooks=True):
        return calls.execute(self, tool, args, key, server, cwd, tool_hooks)

    def hooks(self, event, tool, args, key, result=None, cwd=None, fire=True):
        return calls.hooks(self, event, tool, args, key, result, cwd, fire)

    def tool_hooks(self, params):
        return calls.tool_hooks(self, params)

    def execute_core(self, tool, args, key, server="native"):
        return calls.execute_core(self, tool, args, key, server)

    # --- the commands --------------------------------------------------------

    def shell_argv(self, script):
        return commands.shell_argv(self, script)

    def command_env(self):
        return commands.command_env(self)

    def shell_snapshot(self):
        return commands.shell_snapshot(self)

    def _record(self, command_id):
        return commands._record(self, command_id)

    def start_command(
        self, command_id, argv, cwd, env, *, merge, stdin=None, watched, meta
    ):
        return commands.start_command(
            self,
            command_id,
            argv,
            cwd,
            env,
            merge=merge,
            stdin=stdin,
            watched=watched,
            meta=meta,
        )

    def _wait_command(self, command_id, process):
        return commands._wait_command(self, command_id, process)

    def _tree(self, pid):
        return commands._tree(self, pid)

    def signal_command(self, command_id, number):
        return commands.signal_command(self, command_id, number)

    def stop_command(self, command_id):
        return commands.stop_command(self, command_id)

    def read_command(self, command_id, out_offset=0, err_offset=0, wait=0.0):
        return commands.read_command(self, command_id, out_offset, err_offset, wait)

    def _final_directory(self, record):
        return commands._final_directory(self, record)

    def shell(self, params):
        return commands.shell(self, params)

    def prune_groups(self):
        return commands.prune_groups(self)

    def end_left_behind(self):
        return commands.end_left_behind(self)

    def idle_stop(self, idle_s):
        return commands.idle_stop(self, idle_s)

    def _woke(self, since, slept) -> bool:
        """Whether this process was stopped between the sleep that started at
        ``since`` and now: a wake-up much later than ``slept`` asked for means
        a frozen sandbox ran again, and every clock moved with it. Asked in
        each loop that measures how long something has gone untouched, so a
        freeze is not read as an abandoned command or an idle executor."""
        return time.monotonic() - since > slept * 4 + THAW_JUMP

    def _thawed(self) -> None:
        """Everything the loops measure their waits from is now: the executor
        has just woken, and none of the time it slept counts as time nothing
        used it."""
        now = time.monotonic()
        self.active_at = now
        with self.command_lock:
            for entry in self.running.values():
                entry["read_at"] = now
        self.log("service", "thawed", commands=len(self.running))

    def _reap(self):
        """Stop a watched command whose reader is gone for good, and forget a
        finished one nobody collected (`COMMAND_ABANDONED_S`)."""
        while True:
            since = time.monotonic()
            time.sleep(30)
            if self._woke(since, 30):
                # Frozen meanwhile: the command nobody read did not wait, it
                # slept with the sandbox, and its reader comes back to it.
                self._thawed()
                continue
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
            self.prune_groups()
            for record in list(self.commands_dir.iterdir()):
                try:
                    meta = json.loads((record / "command.json").read_text())
                    finished = (record / "exit").stat().st_mtime
                except (OSError, ValueError):
                    continue
                last = max(finished, self.collected.get(record.name, 0))
                kept = OUTPUT_KEPT_S if meta.get("kept") else COMMAND_ABANDONED_S
                if meta.get("watched") and time.time() - last > kept:
                    shutil.rmtree(record, ignore_errors=True)

    def bash(self, args, key=None):
        return commands.bash(self, args, key)

    def control(self, params):
        return calls.control(self, params)

    # --- the project's context and its files ---------------------------------

    def context(self, known_files=None):
        return context.context(self, known_files)

    def task_fs(self, params):
        return context.task_fs(self, params)

    def repo_search(self, params):
        return context.repo_search(self, params)

    def context_fs(self, params):
        return context.context_fs(self, params)

    def project_tool_hooks(self):
        return context.project_tool_hooks(self)

    def prepare(self, payload):
        return calls.prepare(self, payload)

    # --- the service ---------------------------------------------------------

    def dispatch(self, method, params):
        if isinstance(params, dict) and isinstance(params.get("spec"), dict):
            self.handed_specs[params["server"]] = params["spec"]
        if isinstance(params, dict) and isinstance(params.get("values"), dict):
            self.server_values[params["server"]] = params["values"]
        if isinstance(params, dict) and "allowed" in params:
            self.server_allowed[params["server"]] = params["allowed"]
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
            and params.get("operation") in CONTROL_INSPECT
        ) or (method == "invoke" and params.get("tool") == "TaskStop")
        with self.admission_lock:
            if self.stopping:
                # i18n-exempt: runner bundle: execution machine, stdlib only, no catalog
                raise RuntimeError(
                    "Executor is stopping after sitting idle; request was not accepted"
                )
            if self.upgrading and not inspect_task:
                raise RuntimeError("Executor is upgrading; request was not accepted")
            self.active_calls += 1
            self.active_at = time.monotonic()
        try:
            return self._dispatch(method, params)
        finally:
            with self.admission_lock:
                self.active_calls -= 1
                self.active_at = time.monotonic()

    def _dispatch(self, method, params):
        if method == "prepare":
            return self.prepare(params)
        if method == "configure" or (
            method == "configure_private" and self.config.get("private")
        ):
            # A governed name the prepare does not state, and the room holds the
            # value of, is one to stop using: this only adds, so it would stay.
            for name, value in params.get("env_reset", {}).items():
                if name not in params["env"] and self.env.get(name) == value:
                    self.env.pop(name, None)
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
                    if (path := self.programs / name).is_file():
                        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "pid": os.getpid(),
                "workspace": str(self.root),
                # Where the skills are here (`bootstrap.plant_native_skills`).
                "config_dir": os.environ.get("CLAUDE_CONFIG_DIR"),
                "files": files,
                "runtime_sha256": SOURCE_SHA256,
                "protocol_version": PROTOCOL_VERSION,
                "release": self.config.get("release"),
                "sandbox": bool(self.config.get("sandbox")),  # launch.can_prepare
                "upgrading": self.upgrading,
                "running_commands": len(self.running),
                # Which ones: the sweep stops a command that outlasts its
                # background cap instead of destroying the sandbox around it
                # (`machine.lifecycle`).
                "running_command_ids": list(self.running),
                "capabilities": [
                    "prepare",
                    "idle_upgrade",
                    "machine_files",
                    "machine_git",
                ]
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
        return calls.close(self)


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
                if value["method"] == "shutdown":
                    # A sandboxed pid names the wrong process; Windows has no SIGTERM.
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
        base, address = socketserver.ThreadingTCPServer, ("127.0.0.1", 0)
    else:
        base = socketserver.ThreadingUnixStreamServer
        directory = Path(socket_directory(state))
        directory.mkdir(mode=0o700, exist_ok=True)
        # A fixed name in a shared /tmp: one somebody else made, or a link to
        # somewhere else, would put this socket where they can replace it.
        made = directory.lstat()
        if not stat.S_ISDIR(made.st_mode) or made.st_uid != os.getuid():
            raise RuntimeError(f"{directory} is not this user's own directory")
        directory.chmod(0o700)
        address = str(directory / "executor.sock")
        Path(address).unlink(missing_ok=True)

    class Server(base):
        daemon_threads = False
        # A room's tools call at once; socketserver's queue of 5 refused the
        # rest before the loop accepted them (EAGAIN on dev, 2026-10-06).
        request_queue_size = socket.SOMAXCONN

    path = Path(socket_path(state))
    path.unlink(missing_ok=True)
    with Server(address, Handler) as server:
        if sys.platform == "win32":
            # Written only once the port is listening, so a client that finds
            # the file finds a service behind it.
            write_json(path, {"port": server.server_address[1], "token": token})
        else:
            Path(address).chmod(0o600)
            path.symlink_to(address)

        def stop(_signum, _frame):
            threading.Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        # A private chat's executor is not stopped for sitting idle: its
        # container is reused across turns and `private.ensure` reaches a
        # running container's executor with `request`, never `start`, so one
        # that stopped itself would leave the chat without one until the
        # container is released, which is what bounds it (`private.release`).
        idle_s = float(
            os.environ.get("CHEESE_EXECUTOR_IDLE_EXIT_S", EXECUTOR_IDLE_EXIT_S)
        )
        if not executor.config.get("private"):

            def leave_when_idle():
                while not executor.idle_stop(idle_s):
                    slept = min(EXECUTOR_IDLE_CHECK_S, idle_s / 4)
                    since = time.monotonic()
                    time.sleep(slept)
                    if executor._woke(since, slept):
                        # The sandbox was frozen and has just woken. It did not
                        # sit idle through that: nothing ran that could have
                        # used it. The wake starts the idle clock again rather
                        # than stopping the executor the moment it thaws.
                        executor._thawed()
                    # Every look, so a group that emptied is forgotten before
                    # its number can be handed to a process of somebody else's.
                    executor.prune_groups()
                stop(None, None)

            threading.Thread(target=leave_when_idle, daemon=True).start()
        try:
            server.serve_forever(poll_interval=0.1)
        finally:
            # The socket goes first: from here a caller finds no executor, which
            # it knows how to answer (an install starts one), rather than one
            # that answers and refuses while what follows takes its time.
            path.unlink(missing_ok=True)
            if sys.platform != "win32":
                Path(address).unlink(missing_ok=True)
            if executor.stopping:
                ended = executor.end_left_behind()
                with contextlib.suppress(OSError):
                    executor.log("service", "idle", idle_s=idle_s, ended_groups=ended)
            executor.close()
    # server_close joins request handlers before their receipt database closes.
    executor.db.close()


def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["start", "serve", "request", "stop"])
    parser.add_argument("--state", required=True, type=Path)
    args = parser.parse_args()
    state = args.state if args.state.match("/proc/self/fd/*") else args.state.resolve()
    if args.mode == "serve":
        serve(state)
    elif args.mode == "request":
        value = json.load(sys.stdin)
        print(json.dumps(request(state, value["method"], value.get("params"))))
    elif args.mode == "stop":
        if sys.platform != "win32":
            proxy_record = Path(platform_socket_directory(state)) / "forge-proxy.json"
            if proxy_record.is_file():
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    pid = int(json.loads(proxy_record.read_text())["pid"])
                    os.kill(pid, signal.SIGTERM)
                with contextlib.suppress(OSError):
                    proxy_record.unlink()
        try:
            request(state, "shutdown")
        except (FileNotFoundError, ConnectionRefusedError):
            if not state.exists():
                return
        except RuntimeError:
            # Answered, then refused: old enough that stopping was not a request.
            beside("predecessor")["end_unrequested"](state, args.state)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(state / "service.lock", flags, 0o600), "a") as lock_file:
            for _ in range(int(os.environ.get("CHEESE_EXECUTOR_STOP_TRIES", 100))):
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
                    beside("portable")["popen_daemon"](argv, **options)
                    if sys.platform == "win32"
                    else subprocess.Popen(argv, start_new_session=True, **options)
                )
            # Started is the service answering a ping; failed is the process
            # exiting. Nothing in between says which one is coming, so how long
            # to wait is the caller's call: a fixed count here gave a loaded
            # machine about five seconds and failed services that were only slow.
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Executor startup failed; inspect service.log")
                try:
                    print(json.dumps(request(state, "ping")))
                    return
                except (OSError, RuntimeError):
                    time.sleep(0.05)


if __name__ == "__main__":
    main()
