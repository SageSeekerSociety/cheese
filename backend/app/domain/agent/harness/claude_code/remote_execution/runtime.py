"""Persistent execution service, shipped as a standalone standard-library script.

The Unix socket is reachable only by the owning user. SSH carries MCP to it;
closing an SSH connection does not discard the command registry or replay writes.
"""

from __future__ import annotations

import base64
import contextlib
import fcntl
import hashlib
import json
import os
import queue
import re
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

    def call(self, method, params=None):
        with self.lock:
            self.sequence += 1
            key = self.sequence
            result = self.pending[key] = queue.Queue()
        try:
            self.send(
                {"jsonrpc": "2.0", "id": key, "method": method, "params": params or {}}
            )
            data = result.get(timeout=300)
            if "error" in data:
                raise RuntimeError(data["error"]["message"])
            return data["result"]
        finally:
            self.pending.pop(key, None)

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
        self.config = json.loads((self.state / "config.json").read_text())
        self.root = Path(self.config["workspace"]).resolve(strict=True)
        self.cwd_file = self.state / "cwd.json"
        self.cwd = (
            Path(json.loads(self.cwd_file.read_text()))
            if self.cwd_file.exists()
            else self.root
        )
        self.env = dict(os.environ, **self.config.get("env", {}))
        self.tasks = {}
        self.foreground_ready = {}
        self.cancelled_requests = set()
        self.task_lock = threading.RLock()
        self.cwd_lock = threading.Lock()
        self.clients = {}
        self.client_lock = threading.Lock()
        self.log_lock = threading.Lock()
        self.db_lock = threading.Lock()
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
                    env = dict(
                        self.env,
                        CLAUDE_CONFIG_DIR=str(config_dir),
                        ANTHROPIC_API_KEY="execution-only-no-model",
                        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                    )
                    for key in (
                        "CLAUDE_CODE_OAUTH_TOKEN",
                        "ANTHROPIC_AUTH_TOKEN",
                        "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS",
                    ):
                        env.pop(key, None)
                    env.pop("CLAUDE_CODE_SHELL_PREFIX", None)
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
                        "cwd": str(self.cwd),
                        "session_id": self.state.name,
                    }
                    if event == "PostToolUse":
                        payload["tool_response"] = result
                    response = subprocess.run(
                        ["bash", "-c", hook["command"]],
                        cwd=self.cwd,
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
        if tool not in NATIVE_TOOLS:
            raise ValueError(f"Unsupported remote tool: {tool}")
        if tool == "Read":
            match = re.search(
                r"/tasks/(remote-[0-9a-f]{16})\.output$", args["file_path"]
            )
            if match:
                task = self.output(match.group(1))
                lines = (task["stdout"] + task["stderr"]).splitlines(keepends=True)
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
        args = dict(args)
        for field in ("file_path", "path", "notebook_path"):
            if args.get(field):
                path = Path(args[field])
                args[field] = str(path if path.is_absolute() else self.cwd / path)
        result = self.client("native").call(
            "tools/call", {"name": tool, "arguments": args}
        )
        if result.get("isError"):
            raise RuntimeError(json.dumps(result.get("content")))
        return json.loads(result["content"][0]["text"])

    def bash(self, args, request_id):
        background = args.get("run_in_background", False)
        with self.cwd_lock:
            task_id = "remote-" + uuid.uuid4().hex[:16]
            directory = self.state / "tasks" / task_id
            directory.mkdir(parents=True)
            command_file = directory / "command.sh"
            command_file.write_text(args["command"])
            cwd_path = directory / "cwd"
            # The exit trap records cd even when the command calls exit.
            trap = "printf '%s' \"$PWD\" > " + shlex.quote(str(cwd_path))
            shell = (
                "trap "
                + shlex.quote(trap)
                + " EXIT\nsource "
                + shlex.quote(str(command_file))
            )
            (directory / "stdin").write_text(args.get("stdin", ""))
            with self.task_lock:
                if request_id in self.cancelled_requests:
                    raise RuntimeError("Command was cancelled before starting")
                with (
                    (directory / "stdout").open("wb") as stdout,
                    (directory / "stderr").open("wb") as stderr,
                    (directory / "stdin").open("rb") as stdin,
                ):
                    process = subprocess.Popen(
                        ["bash", "-c", shell],
                        cwd=self.cwd,
                        env=self.env,
                        stdin=stdin,
                        stdout=stdout,
                        stderr=stderr,
                        start_new_session=True,
                    )
                task = {
                    "id": task_id,
                    "request_id": request_id,
                    "command": args["command"],
                    "description": args.get("description", args["command"]),
                    "pid": process.pid,
                    "status": "running",
                    "started_at": stamp(),
                    "exit_code": None,
                    "background": background,
                }
                self.tasks[task_id] = (task, process, threading.Event())
                self.foreground_ready[task_id] = threading.Event()
                write_json(directory / "status.json", task)
            threading.Thread(target=self._watch, args=(task_id,), daemon=True).start()
            if not background:
                self.foreground_ready[task_id].wait(
                    min(max(args.get("timeout", 120000), 1), 600000) / 1000
                )
            if task["status"] == "running":
                task["background"] = True
                return {
                    "stdout": "",
                    "stderr": "",
                    "interrupted": False,
                    "backgroundTaskId": task_id,
                    "noOutputExpected": False,
                }
            if cwd_path.exists():
                self.cwd = Path(cwd_path.read_text())
                write_json(self.cwd_file, str(self.cwd))
            result = self.output(task_id)
            return {
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "interrupted": task["status"] == "stopped",
                "noOutputExpected": False,
                "returnCodeInterpretation": f"Exit code {task['exit_code']}",
            }

    def _watch(self, task_id):
        task, process, done = self.tasks[task_id]
        code = process.wait()
        with self.task_lock:
            if task["status"] != "stopped":
                task["status"] = "completed" if code == 0 else "failed"
            task.update(exit_code=code, finished_at=stamp())
            write_json(self.state / "tasks" / task_id / "status.json", task)
            self.log(task_id, task["status"], exit_code=code)
        done.set()
        self.foreground_ready[task_id].set()

    def output(self, task_id):
        task = self.task(task_id)
        directory = self.state / "tasks" / task_id
        return {
            **task,
            **{
                name: (directory / name).read_text(errors="replace")
                for name in ("stdout", "stderr")
            },
        }

    def task(self, task_id):
        if not task_id.startswith("remote-") or "/" in task_id:
            raise ValueError("Invalid task ID")
        if task_id in self.tasks:
            return dict(self.tasks[task_id][0])
        path = self.state / "tasks" / task_id / "status.json"
        if not path.exists():
            raise ValueError("Unknown remote task")
        task = json.loads(path.read_text())
        if task["status"] == "running":
            task["status"] = "unknown"
        return task

    def task_output(self, args):
        task_id = args["task_id"]
        if args.get("block", True) and task_id in self.tasks:
            self.tasks[task_id][2].wait(
                min(max(args.get("timeout", 30000), 0), 600000) / 1000
            )
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
                "output": output["stdout"] + output["stderr"],
                "exitCode": output["exit_code"],
            },
        }

    def stop_task(self, task_id):
        self.task(task_id)
        if task_id not in self.tasks:
            raise RuntimeError(
                "Task process identity was lost; refusing to signal a recycled PID"
            )
        task, process, done = self.tasks[task_id]
        with self.task_lock:
            if task["status"] != "running":
                return dict(task)
            task["status"] = "stopped"
        # Kill the group even when its leader exits first; descendants may ignore TERM.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        done.wait(0.3)
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
        done.wait(5)
        return dict(task)

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
                for task_id, (task, _, _) in self.tasks.items():
                    if task["request_id"] == params.get("tool_use_id"):
                        task["background"] = True
                        self.foreground_ready[task_id].set()
            return {
                "tasks": [
                    {
                        **self.task(p.parent.name),
                        "task_id": p.parent.name,
                        "task_type": "local_bash",
                    }
                    for p in (self.state / "tasks").glob("*/status.json")
                ]
            }
        if kind == "stop_task":
            task = self.stop_task(params["task_id"])
            return {"task_id": task["id"], "status": task["status"]}
        if kind == "stop_request":
            with self.task_lock:
                self.cancelled_requests.add(params["request_id"])
                selected = [
                    task_id
                    for task_id, (task, _, _) in self.tasks.items()
                    if task["request_id"] == params["request_id"]
                ]
            for task_id in selected:
                self.stop_task(task_id)
            return {}
        if kind == "task_output":
            return self.output(params["task_id"])
        if kind == "interrupt":
            for task_id, (task, _, _) in list(self.tasks.items()):
                if task["status"] == "running" and not task["background"]:
                    self.stop_task(task_id)
            return {}
        raise ValueError(f"Unsupported executor control: {kind}")

    def context(self):
        if self.config.get("private"):
            # Never import executable configuration written by a scratch command
            # into the central Claude Code process.
            return {
                "workspace": str(self.root),
                "cwd": str(self.cwd),
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
        for path in paths:
            if path.is_file():
                files[str(path.relative_to(self.root))] = base64.b64encode(
                    path.read_bytes()
                ).decode()
        return {
            "workspace": str(self.root),
            "cwd": str(self.cwd),
            "files": files,
            "instructions": "\n\n".join(instructions),
        }

    def dispatch(self, method, params):
        if method == "configure" or (
            method == "configure_private" and self.config.get("private")
        ):
            self.env.update(params["env"])
            self.config["env"] = params["env"]
            write_json(self.state / "config.json", self.config)
            return {"pid": os.getpid(), "workspace": str(self.root)}
        if method == "ping":
            return {"pid": os.getpid(), "workspace": str(self.root)}
        if method == "invoke":
            return self.invoke(params)
        if method == "control":
            return self.control(params)
        if method == "context":
            return self.context()
        if method == "mcp":
            return self.client(params["server"]).call(
                params["method"], params.get("params")
            )
        raise ValueError("Unknown executor method")

    def close(self):
        for task_id, (task, _, _) in list(self.tasks.items()):
            if task["status"] == "running":
                self.stop_task(task_id)
        for client in self.clients.values():
            client.close()


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
