"""Persistent execution service, shipped as a standalone standard-library script.

The Unix socket is reachable only by the owning user. SSH carries MCP to it;
closing an SSH connection does not discard the command registry or replay writes.
"""

from __future__ import annotations

import array
import base64
import contextlib
import fcntl
import hashlib
import json
import os
import queue
import re
import runpy
import select
import shlex
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

SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()

NATIVE_TOOLS = {
    "Read",
    "Edit",
    "Write",
    "Bash",
    "NotebookEdit",
    "TaskOutput",
    "TaskStop",
}
FILE_TOOLS = NATIVE_TOOLS - {"Bash", "TaskOutput", "TaskStop"}


def stamp():
    # This shipped script also runs on hosts with Python 3.9.
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def socket_path(state):
    digest = hashlib.sha256(str(Path(state).resolve()).encode()).hexdigest()[:24]
    return f"/tmp/cheese-execution-{os.getuid()}-{digest}.sock"


def request(state, method, params=None):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(socket_path(state))
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
    response = json.loads(line)
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


# `claude mcp serve` writes a backgrounded command's output to
# `<CLAUDE_CODE_TMPDIR>/claude-<uid>/<workspace>/<session>/tasks/<id>.output`,
# and reading that file is the only way to get it: from serve mode the build
# offers no way back to the task — `TaskStop` looks it up in an app state the
# serve entrypoint throws away and answers `No task found with ID`, and 2.1.277
# stopped serving `TaskOutput` there at all.
# The session component is a UUID the process picks and never tells us — it is
# deliberately unguessable, so a shared /tmp cannot be pre-empted — hence the
# glob rather than a built path. We point CLAUDE_CODE_TMPDIR at the room's own
# state directory, so the glob stays inside one room.
# `scripts/remote_execution/mcp_contract.py` is what tells us this still holds.
def serve_task_output(temp_root, task_id):
    matches = sorted(Path(temp_root).glob(f"*/*/*/tasks/{task_id}.output"))
    return matches[0] if matches else None


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
        self.root = Path(self.config["workspace"]).resolve(strict=True)
        self.env = dict(os.environ, **self.config.get("env", {}))
        # Every command this executor has run through `mcp serve`, by marker:
        # the marker is the only thing that ties a process in `ps` to a task,
        # and the only thing that survives the serve process forgetting the
        # task the moment it hands out its id.
        self.tasks = {}
        self.by_task_id = {}
        # One event per running command: set by the room's "move to
        # background" or a stop, and what the foreground wait watches.
        self.released = {}
        self.cancelled_requests = set()
        self.task_lock = threading.RLock()
        self.cli_lock = threading.Lock()
        self.serve_temp = self.state / "serve-temp"
        self.serve_temp.mkdir(exist_ok=True)
        for record in (self.state / "tasks").glob("cheese-task-*/task.json"):
            task = json.loads(record.read_text())
            self.tasks[task["marker"]] = task
            if task.get("task_id"):
                self.by_task_id[task["task_id"]] = task["marker"]
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
        worker = self.state.parent / "remote-execution/cli_worker.py"
        cli = self.state.parent / "cheese"
        if worker.is_file() and cli.is_file():
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
                    if self.cli_worker is not None:
                        # Commands run from this process, so the CLI has to be
                        # on its PATH and reachable before it starts.
                        self._ready_cli_worker()
                    # This process runs the room's shell commands as well as
                    # its file operations, so it carries the room's own
                    # environment, credentials included: a command of the
                    # room's reaches whatever the room reaches, and stripping
                    # them would only break the commands.
                    env = dict(
                        self.env,
                        CLAUDE_CONFIG_DIR=str(config_dir),
                        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                        CLAUDE_CODE_TMPDIR=str(self.serve_temp),
                        PATH=str(self.state.parent / "remote-execution/bin")
                        + os.pathsep
                        + self.env.get("PATH", os.defpath),
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

    # Outside `cwd_lock` deliberately: a foreground shell command holds that
    # lock for as long as it runs, up to ten minutes, and nothing about a
    # platform command needs the shell to be idle — the worker forks per
    # request and only reads `cwd`. Taking it here cost a room its entire
    # session on 2026-09-17: the `tools/list` a new session must answer queued
    # behind a 60s command, Claude Code dropped the native server at its own
    # 30s deadline, and every file, shell and chat tool was denied until the
    # session was relaunched.
    def cli(self, params):
        self._ready_cli_worker()
        call_id = "cli-" + uuid.uuid4().hex[:16]
        directory = self.state / "tasks" / call_id
        directory.mkdir(parents=True)
        paths = [directory / name for name in ("stdin", "stdout", "stderr")]
        paths[0].write_text(params.get("stdin", ""))
        with (
            paths[0].open("rb") as stdin,
            paths[1].open("wb") as stdout,
            paths[2].open("wb") as stderr,
            socket.socket(socket.AF_UNIX) as connection,
        ):
            connection.connect(self.env["CHEESE_CLI_SOCKET"])
            connection.sendmsg(
                [b"\0"],
                [
                    (
                        socket.SOL_SOCKET,
                        socket.SCM_RIGHTS,
                        array.array(
                            "i", [stdin.fileno(), stdout.fileno(), stderr.fileno()]
                        ),
                    )
                ],
            )
            connection.sendall(
                json.dumps(
                    {
                        "mcp": params,
                        # A listing needs no directory, and asking the serve
                        # process for one on a session's first listing means
                        # waiting for that process to start — which is the
                        # listing arriving after the first tool call again.
                        "cwd": str(
                            self.root
                            if params.get("method") == "tools/list"
                            else self.current_directory()
                        ),
                        "env": self.env,
                        "stdio": [
                            {"encoding": "utf-8", "errors": "strict"} for _ in range(3)
                        ],
                    }
                ).encode()
                + b"\n"
            )
            with connection.makefile("rb") as stream:
                line = stream.readline()
        if not line:
            raise RuntimeError(
                "CLI worker disconnected; query the platform before retrying a write"
            )
        receipt = json.loads(line)
        if "result" in receipt:
            return receipt["result"]
        result = {
            "stdout": paths[1].read_text(),
            "stderr": paths[2].read_text(),
            "exit_code": receipt["status"],
        }
        if receipt["status"]:
            raise RuntimeError(result["stderr"] or f"Cheese exited {receipt['status']}")
        return result

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

    def execute(self, tool, args, key, server="native"):
        name = tool if server == "native" else f"mcp__{server}__{tool}"
        args = self.hooks("PreToolUse", name, args, key)
        result = self.execute_core(tool, args, key, server)
        self.hooks("PostToolUse", name, args, key, result)
        return result

    def hooks(self, event, tool, args, key, result=None):
        if self.config.get("private"):
            return args
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
                        "cwd": str(self.current_directory()),
                        "session_id": self.state.name,
                    }
                    if event == "PostToolUse":
                        payload["tool_response"] = result
                    response = subprocess.run(
                        ["bash", "-c", hook["command"]],
                        cwd=self.current_directory(),
                        env=self.env,
                        input=json.dumps(payload),
                        capture_output=True,
                        text=True,
                        timeout=hook.get("timeout", 60),
                    )
                    if response.returncode:
                        raise PermissionError(
                            f"Remote {event} hook failed: {response.stderr}"
                        )
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
        if tool.startswith("mcp__native__cheese_"):
            return self.cli(
                {
                    "method": "tools/call",
                    "tool": tool.removeprefix("mcp__native__"),
                    "arguments": args,
                }
            )
        if tool not in NATIVE_TOOLS:
            raise ValueError(f"Unsupported remote tool: {tool}")
        if tool == "Read":
            # The central session tells the agent a background task's output
            # is at `<its own temp dir>/tasks/<id>.output`, a path that exists
            # on no machine; the task's output is what that read means.
            match = re.search(
                r"/tasks/(cheese-task-[0-9a-f]{16})\.output$", args["file_path"]
            )
            if match:
                task = self.output(match.group(1))
                lines = task["stdout"].splitlines(keepends=True)
                start = max(args.get("offset", 1), 1)
                selected = lines[start - 1 : start - 1 + args.get("limit", 2000)]
                return {
                    "type": "text",
                    "file": {
                        "filePath": args["file_path"],
                        "content": "".join(selected),
                        "numLines": len(selected),
                        "startLine": start,
                        "totalLines": len(lines),
                    },
                }
        if tool == "Bash":
            return self.bash(args, key)
        if tool == "TaskOutput":
            return self.task_output(args)
        if tool == "TaskStop":
            task = self.stop_task(args["task_id"])
            return {
                "message": "Remote process group stopped",
                "task_id": task["id"],
                "task_type": "local_bash",
                "command": task["command"],
            }
        result = self.client("native").call(
            "tools/call", {"name": tool, "arguments": args}
        )
        if result.get("isError"):
            raise RuntimeError(json.dumps(result.get("content")))
        return json.loads(result["content"][0]["text"])

    def current_directory(self):
        """The shell's working directory, which lives in the serve process."""
        answer = self.client("native").call(
            "tools/call", {"name": "Bash", "arguments": {"command": "pwd"}}
        )
        return Path(json.loads(answer["content"][0]["text"])["stdout"].strip())

    # `mcp serve` runs the command, so a `cd` that outlives the call, a command
    # that leaves the workspace being put back, and the text a failure comes
    # back as are the build's own answers rather than ours. Two things stay
    # ours. The wait: at the build's own foreground timeout a command is either
    # killed or turned into a background task, and which one depends on whether
    # it printed anything right at the start — measured on 2.1.265,
    # `printf x; sleep 8` is backgrounded and `sleep 1; echo x; sleep 8` is
    # killed, a rule written down nowhere. So the serve call gets the largest
    # timeout there is, and the agent's timeout, the room's "move to
    # background" and "interrupt" are all decided here, by no longer waiting
    # for an answer the command goes on producing. And the way back to a task:
    # the build offers none from serve mode — `TaskStop` looks an id up in an
    # app state the serve entrypoint discards and answers `No task found` for
    # the id its own Bash tool just handed out, and 2.1.277 stopped serving
    # `TaskOutput` there at all. Hence
    # two lines around every command — a marker that puts the task in the
    # shell's argv, which is how `ps` says which process is which task, and an
    # EXIT trap that records the exit status, which is how a task is known to
    # have finished and how. A trap rather than trailing lines: a command that
    # calls `exit` itself never reaches anything appended after it, and an
    # `exit` of our own is what stops the build recording the new directory.
    # `scripts/remote_execution/mcp_contract.py` says whether this still holds.
    def bash(self, args, request_id):
        marker = "cheese-task-" + uuid.uuid4().hex[:16]
        record = self.state / "tasks" / marker
        record.mkdir(parents=True)
        task = {
            "marker": marker,
            "task_id": None,
            "request_id": request_id,
            "command": args["command"],
            "description": args.get("description", args["command"]),
            "background": bool(args.get("run_in_background", False)),
            "status": "running",
            "exit_code": None,
            "started_at": stamp(),
            "started_ts": time.time(),
        }
        with self.task_lock:
            if request_id in self.cancelled_requests:
                raise RuntimeError("Command was cancelled before starting")
            self.tasks[marker] = task
            self.released[marker] = threading.Event()
            write_json(record / "task.json", task)
        self.log(marker, "started", command=args["command"])
        arguments = {
            "command": (
                f": {marker}\n"
                # The path goes through a variable: quoted inline it would sit
                # inside the trap's own quotes, and a space in it broke every
                # command.
                f"__cheese_exit={shlex.quote(str(record / 'exit'))}\n"
                'trap \'printf %s "$?" > "$__cheese_exit"\' EXIT\n'
                # The serve process keeps the environment it started with, and
                # the room's part of it changes underneath it: a refreshed
                # token arrives through `configure`. So every command exports
                # the room's current values itself.
                + "".join(
                    f"export {name}={shlex.quote(str(value))}\n"
                    for name, value in self.config.get("env", {}).items()
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name)
                )
                + args["command"]
                + "\n"
            ),
            "timeout": 600000,
            **{
                name: args[name]
                for name in ("run_in_background", "description")
                if args.get(name) is not None
            },
        }
        client = self.client("native")
        if task["background"]:
            return self._deliver(
                marker,
                client.call("tools/call", {"name": "Bash", "arguments": arguments}),
            )
        key, answers = client.begin(
            "tools/call", {"name": "Bash", "arguments": arguments}
        )
        deadline = (
            time.monotonic() + min(max(args.get("timeout", 120000), 1), 600000) / 1000
        )
        released = self.released[marker]
        while True:
            try:
                data = answers.get(timeout=0.1)
                break
            except queue.Empty:
                if released.is_set() or time.monotonic() >= deadline:
                    data = None
                    break
        if data is not None:
            client.pending.pop(key, None)
            if "error" in data:
                raise RuntimeError(data["error"]["message"])
            return self._deliver(marker, data["result"])
        # The command goes on; its answer is collected when it comes.
        with self.task_lock:
            task["background"] = True
            write_json(record / "task.json", task)
        threading.Thread(
            target=self._collect, args=(marker, client, key, answers), daemon=True
        ).start()
        return {
            "stdout": "",
            "stderr": "",
            "interrupted": False,
            "noOutputExpected": False,
            "backgroundTaskId": marker,
        }

    def _collect(self, marker, client, key, answers):
        try:
            self._deliver(marker, client.finish(key, answers, timeout=660))
        except Exception as exc:  # noqa: BLE001 — the record says what happened
            self.log(marker, "lost", error=str(exc))
            self._settle(marker)

    def _deliver(self, marker, result):
        """Record what the serve process answered for a command, and shape it."""
        task = self.tasks[marker]
        record = self.state / "tasks" / marker
        text = result["content"][0]["text"]
        if result.get("isError"):
            # A command that exits non-zero is not a failure of the executor.
            # This is the text the build gives its own agent: the exit code
            # line first, then whatever the command printed.
            (record / "output").write_text(text)
            settled = self._settle(marker)
            return {
                "stdout": text,
                "stderr": "",
                "interrupted": settled["status"] == "stopped",
                "noOutputExpected": False,
            }
        value = json.loads(text)
        if value.get("backgroundTaskId"):
            with self.task_lock:
                task["task_id"] = value["backgroundTaskId"]
                task["background"] = True
                self.by_task_id[task["task_id"]] = marker
                write_json(record / "task.json", task)
            # The room sees one kind of task id, ours, whichever side started
            # the background task.
            return {**value, "backgroundTaskId": marker}
        (record / "output").write_text(
            value.get("stdout", "") + value.get("stderr", "")
        )
        self._settle(marker)
        return value

    def _settle(self, marker):
        """Record how a command ended, from the exit file its trap wrote."""
        task = self.tasks[marker]
        exit_file = self.state / "tasks" / marker / "exit"
        with self.task_lock:
            before = task["status"]
            if before in ("running", "unknown"):
                if exit_file.exists():
                    code = int(exit_file.read_text() or 1)
                    task.update(
                        status="completed" if code == 0 else "failed",
                        exit_code=code,
                        finished_at=stamp(),
                    )
                elif self._pids(marker):
                    task["status"] = "running"
                elif time.time() - task["started_ts"] > 2:
                    # Nothing wrote an exit status and nothing carries the
                    # marker. The serve process answers with a task id before
                    # the shell is even listed, so this only counts once the
                    # command has had time to appear.
                    task.update(status="unknown", finished_at=stamp())
            if task["status"] != before:
                write_json(self.state / "tasks" / marker / "task.json", task)
                if task["status"] != "running":
                    self.log(marker, task["status"], exit_code=task["exit_code"])
        return dict(task)

    def _pids(self, marker):
        """Processes carrying the marker in their command line, parents first."""
        rows = []
        if Path("/proc").is_dir():
            # /proc rather than `ps`: the private execution image has no `ps`.
            for entry in Path("/proc").iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    argv = (entry / "cmdline").read_bytes().replace(b"\0", b" ")
                    stat = (entry / "stat").read_text()
                except OSError:
                    continue
                # The command name sits in parentheses and may hold spaces;
                # the parent pid is the second field after it.
                ppid = stat[stat.rindex(")") + 2 :].split()[1]
                rows.append((entry.name, ppid, argv.decode(errors="replace")))
        else:
            listing = subprocess.run(
                ["ps", "-eo", "pid=,ppid=,args="], capture_output=True, text=True
            ).stdout
            rows = [
                tuple(row)
                for row in (line.split(None, 2) for line in listing.splitlines())
                if len(row) == 3
            ]
        seeds = [
            int(pid)
            for pid, _, argv in rows
            if marker in argv and str(os.getpid()) != pid
        ]
        children = {}
        for pid, ppid, _ in rows:
            children.setdefault(int(ppid), []).append(int(pid))
        found, queue_ = [], list(seeds)
        while queue_:
            pid = queue_.pop(0)
            if pid not in found:
                found.append(pid)
                queue_.extend(children.get(pid, []))
        return found

    def _marker(self, task_id):
        if task_id in self.by_task_id:
            return self.by_task_id[task_id]
        if task_id in self.tasks:
            return task_id
        raise ValueError("Unknown remote task")

    def task(self, task_id):
        return self._settle(self._marker(task_id))

    def output(self, task_id):
        task = self.task(task_id)
        record = self.state / "tasks" / task["marker"]
        if (record / "output").exists():
            text = (record / "output").read_text(errors="replace")
        elif task.get("task_id"):
            output = serve_task_output(self.serve_temp, task["task_id"])
            text = output.read_text(errors="replace") if output else ""
        else:
            text = ""
        return {**task, "id": task["marker"], "stdout": text, "stderr": ""}

    def task_output(self, args):
        task_id = args["task_id"]
        marker = self._marker(task_id)
        record = self.state / "tasks" / marker
        deadline = (
            time.monotonic() + min(max(args.get("timeout", 30000), 0), 600000) / 1000
        )
        while args.get("block", True) and time.monotonic() < deadline:
            task = self.task(marker)
            recorded = (record / "output").exists() or bool(
                task.get("task_id")
                and serve_task_output(self.serve_temp, task["task_id"])
            )
            if task["status"] != "running" and (
                recorded or task["status"] == "unknown"
            ):
                break
            time.sleep(0.1)
        output = self.output(task_id)
        return {
            "retrieval_status": "timeout"
            if output["status"] == "running"
            else "success",
            "task": {
                "task_id": task_id,
                "task_type": "local_bash",
                "status": output["status"],
                "description": output["description"],
                "output": output["stdout"],
                "exitCode": output["exit_code"],
            },
        }

    def stop_task(self, task_id):
        marker = self._marker(task_id)
        task = self.tasks[marker]
        with self.task_lock:
            if task["status"] != "running":
                return {**task, "id": marker}
            task["status"] = "stopped"
            task["finished_at"] = stamp()
            write_json(self.state / "tasks" / marker / "task.json", task)
        # The processes are the serve process's children, so its process group
        # is not ours to signal; each one is killed by pid. The tree is taken
        # once, before TERM: a descendant that ignores TERM outlives the shell
        # that carried the marker and is re-parented, so a second look would
        # not find it.
        pids = self._pids(marker)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGTERM)
        time.sleep(0.3)
        for pid in pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        self.log(marker, "stopped")
        return {**task, "id": marker}

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
        if kind == "stage_file":
            path = (self.root / params["path"]).resolve()
            path.relative_to(self.root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(params["data"], validate=True))
            return {"path": str(path)}
        if kind == "read_file":
            path = Path(params["path"])
            if not path.is_absolute():
                path = self.root / path
            return {"contents": path.read_text(), "absPath": str(path)}
        if kind == "file_suggestions":
            query = params.get("query", "").casefold()
            if self.config.get("private"):
                files = [
                    str(p.relative_to(self.root))
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
        if kind == "background_tasks":
            with self.task_lock:
                for marker, task in self.tasks.items():
                    if task["request_id"] == params.get("tool_use_id"):
                        self.released[marker].set()
            return {
                "tasks": [
                    {
                        **self.task(marker),
                        "task_id": task.get("task_id") or marker,
                        "task_type": "local_bash",
                    }
                    for marker, task in list(self.tasks.items())
                ]
            }
        if kind == "stop_task":
            task = self.stop_task(params["task_id"])
            return {"task_id": task["id"], "status": task["status"]}
        if kind == "stop_request":
            with self.task_lock:
                self.cancelled_requests.add(params["request_id"])
                selected = [
                    marker
                    for marker, task in self.tasks.items()
                    if task["request_id"] == params["request_id"]
                ]
            for marker in selected:
                self.stop_task(marker)
            return {}
        if kind == "task_output":
            return self.output(params["task_id"])
        if kind == "interrupt":
            for marker, task in list(self.tasks.items()):
                if task["status"] == "running" and not task["background"]:
                    self.stop_task(marker)
            return {}
        raise ValueError(f"Unsupported executor control: {kind}")

    def context(self, known_files=None):
        if self.config.get("private"):
            # Never import executable configuration written by a scratch command
            # into the central Claude Code process.
            return {
                "workspace": str(self.root),
                "cwd": str(self.current_directory()),
                "files": {},
                "instructions": "This chat has temporary scratch space at /work. "
                "Use shell and file tools for drafts and small processing tasks. "
                "Save finished documents through cheese doc set and publish artifacts "
                "through cheese artifact. Scratch files can disappear when execution "
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
                name = str(path.relative_to(self.root))
                file_names.append(name)
                content = path.read_bytes()
                if (known_files or {}).get(name) != hashlib.sha256(content).hexdigest():
                    files[name] = base64.b64encode(content).decode()
        return {
            "workspace": str(self.root),
            "cwd": str(self.current_directory()),
            "files": files,
            "file_names": file_names,
            "instructions": "\n\n".join(instructions),
        }

    def context_fs(self, params):
        """Expose the native project context as a bounded read-only file view."""
        if self.config.get("private"):
            return {"generation": hashlib.sha256(b"private").hexdigest(), "entries": {}}

        root = self.root.resolve()
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
                        unsupported_paths.add(str(path.relative_to(root)))
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
                    unsupported_paths.add(str(path.relative_to(root)))
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
        include(root / ".claude/skills")

        entries = {}
        for path in selected:
            relative = str(path.relative_to(root))
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
                name = str(parent.relative_to(root))
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
            }
        raise ValueError("Unsupported context filesystem operation")

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
        bootstrap = runpy.run_path(
            str(self.state.parent / "remote-execution/bootstrap.py")
        )
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
                runner = runpy.run_path(
                    str(self.state.parent / "cheese-environment.py")
                )
                info["environment_status"] = runner["read_status"](
                    home / ".cheese-environment"
                )["state"]
            return info

    def dispatch(self, method, params):
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
            manifest = self.state.parent / "executor-files.json"
            files = {}
            if manifest.exists():
                for name in json.loads(manifest.read_text()):
                    path = self.state.parent / name
                    if path.is_file():
                        files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            return {
                "pid": os.getpid(),
                "workspace": str(self.root),
                "files": files,
                "runtime_sha256": SOURCE_SHA256,
                "capabilities": ["prepare"]
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
        if method == "mcp":
            return self.client(params["server"]).call(
                params["method"], params.get("params")
            )
        if method == "cli":
            return self.cli(params)
        raise ValueError("Unknown executor method")

    def close(self):
        for marker, task in list(self.tasks.items()):
            if task["status"] == "running":
                self.stop_task(marker)
        for client in self.clients.values():
            client.close()
        if self.cli_worker is not None:
            assert self.cli_worker.stdin is not None
            self.cli_worker.stdin.close()
            self.cli_worker.wait(timeout=10)


def serve(state):
    lock = (state / "service.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    executor = Executor(state)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            try:
                value = json.loads(self.rfile.readline())
                response = {
                    "result": executor.dispatch(
                        value["method"], value.get("params", {})
                    )
                }
            except Exception as exc:
                response = {"error": str(exc)}
            with contextlib.suppress(BrokenPipeError):
                self.wfile.write(json.dumps(response).encode() + b"\n")

    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = False

    path = Path(socket_path(state))
    path.unlink(missing_ok=True)
    with Server(str(path), Handler) as server:
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
            return
        os.kill(info["pid"], signal.SIGTERM)
        for _ in range(100):
            if not Path(socket_path(state)).exists():
                return
            time.sleep(0.1)
        raise RuntimeError("Executor did not stop")
    else:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        configuration = json.load(sys.stdin)
        start_lock = (state / "start.lock").open("a")
        with start_lock:
            fcntl.flock(start_lock, fcntl.LOCK_EX)
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
            with (state / "service.log").open("a") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(Path(__file__).resolve()),
                        "serve",
                        "--state",
                        str(state),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
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
