"""The tool calls: what a caller asks this executor to run, and its answer.

The admission gate around a call and its receipt (`invoke`), the project's
hooks around it (`execute`, `hooks`, `tool_hooks`), the native or stdio MCP
servers it is handed to (`client`), the calls that are not a tool at all
(`control`), and the preparation an install asks for (`prepare`). `Executor` in
`runtime.py` forwards to these bodies.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import runpy
import select
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

if __package__:
    from . import runtime_core as core
else:
    # A script run has this file's directory first on `sys.path`; one loaded by
    # path — `bootstrap` installs one, `predecessor` loads the release's — has
    # not, and the module it reads is the one beside it.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    import runtime_core as core

# What the bodies below read of the runtime's primitives, named here the way
# they were named when they were `Executor`'s methods.
NATIVE_TOOLS = core.NATIVE_TOOLS
beside = core.beside
claude_shell = core.claude_shell
native_path = core.native_path
project_hooks = core.project_hooks
resolve_program = core.resolve_program
socket_directory = core.socket_directory
write_json = core.write_json


#: What starts the error a local server's start is refused with on a member's
#: computer whose owner has not allowed its command; a JSON object follows.
APPROVAL_REQUIRED = "MCP_APPROVAL_REQUIRED "
_VARIABLE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def server_fingerprint(spec):
    """What tells one local server's command from another: its definition as
    written, `${VAR}` unexpanded, so a secret changing is not a new command."""
    written = {
        "command": spec.get("command"),
        "args": list(spec.get("args", [])),
        "cwd": spec.get("cwd"),
        "env": dict(spec.get("env", {})),
    }
    return hashlib.sha256(json.dumps(written, sort_keys=True).encode()).hexdigest()


def expand_variables(text, values):
    """`${VAR}` and `${VAR:-default}`, as `.mcp.json` writes them; a variable
    with neither a value nor a default is left as written."""

    def replace(match):
        name, default = match.group(1), match.group(2)
        if name in values:
            return values[name]
        return default if default is not None else match.group(0)

    return _VARIABLE.sub(replace, text)


def log_tail(path, lines=12):
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])


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
                # included, with the platform CLI on PATH — and is held to
                # the shell commands here run in, which is the one it would
                # choose itself.
                env = dict(
                    self.command_env(),
                    CLAUDE_CONFIG_DIR=str(config_dir),
                    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                    CLAUDE_CODE_TMPDIR=str(self.state / "serve-temp"),
                    CLAUDE_CODE_SHELL=claude_shell(self.env),
                )
                for key in (
                    "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS",
                    "CLAUDE_CODE_SHELL_PREFIX",
                ):
                    env.pop(key, None)
                cwd = self.root
            else:
                spec = {**self.handed_specs, **self.config["mcp_servers"]}[server]
                if spec.get("type", "stdio") != "stdio":
                    raise ValueError("Remote process servers require stdio transport")
                allowed = self.server_allowed.get(server)
                if isinstance(allowed, list):
                    # A member's own computer runs only the commands its owner
                    # allowed, told apart by what is written.
                    fingerprint = server_fingerprint(spec)
                    if fingerprint not in allowed:
                        raise RuntimeError(
                            APPROVAL_REQUIRED
                            + json.dumps(
                                {
                                    "fingerprint": fingerprint,
                                    "command": shlex.join(
                                        [spec["command"], *spec.get("args", [])]
                                    ),
                                }
                            )
                        )
                known = {**self.env, **self.server_values.get(server, {})}
                command = [
                    expand_variables(part, known)
                    for part in [spec["command"], *spec.get("args", [])]
                ]
                env = dict(
                    self.env,
                    **{
                        key: expand_variables(str(value), known)
                        for key, value in spec.get("env", {}).items()
                    },
                )
                cwd = expand_variables(spec.get("cwd", str(self.root)), known)
            log = (
                self.state
                / ("mcp-" + hashlib.sha256(server.encode()).hexdigest()[:12] + ".log")
            ).open("a")
            try:
                process = beside("mcp_process")["MCPProcess"]
                self.clients[server] = process(command, cwd, env, log)
            except Exception as exc:
                # What it printed before it gave up says why; the session
                # shows the end of it.
                log.close()
                # i18n-exempt: runner bundle: execution machine, stdlib only
                raise RuntimeError(f"{exc}\n{log_tail(Path(log.name))}") from exc
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
        self.env["CHEESE_CLI_SOCKET"] = str(
            Path(socket_directory(self.state)) / "cli.sock"
        )
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
    args = params.get("args", {})
    if not params.get("platform"):
        # `platform_env` names variables for one command, and only the
        # platform's own calls may (`checkpoint`'s turn). A call from the
        # model's own tools is not one, whatever it puts in its arguments.
        args = {k: v for k, v in args.items() if k != "platform_env"}
    try:
        result = {
            "value": self.execute(
                params["tool"],
                args,
                key,
                params.get("server", "native"),
                params.get("cwd"),
                tool_hooks=not params.get("platform"),
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


def execute(self, tool, args, key, server="native", cwd=None, tool_hooks=True):
    name = tool if server == "native" else f"mcp__{server}__{tool}"
    if tool_hooks:
        args = self.hooks("PreToolUse", name, args, key, cwd=cwd)
    result = self.execute_core(tool, args, key, server)
    if tool_hooks:
        self.hooks("PostToolUse", name, args, key, result, cwd=cwd)
    return result


def hooks(self, event, tool, args, key, result=None, cwd=None, fire=True):
    if self.config.get("private"):
        return args
    return project_hooks()["run"](
        event,
        tool,
        args,
        call_id=key,
        root=self.root,
        cwd=Path(cwd) if cwd and Path(cwd).is_dir() else self.bash_cwd,
        env=self.env,
        session_id=self.state.name,
        result=result,
        extra=[self.config.get("settings", {})],
        program=resolve_program,
        fire=fire,
    )


def tool_hooks(self, params):
    """The project's hooks for one event of a call this executor does not
    run, asked before and after it by a harness that fires none itself; or,
    with `fire` false, only its `permissions.deny`, for one whose hooks the
    build fires. A deny is an answer: the caller must be able to say why."""
    try:
        args = self.hooks(
            params["event"],
            params["tool"],
            params.get("args", {}),
            params["request_id"],
            params.get("result"),
            cwd=params.get("cwd"),
            fire=params.get("fire", True),
        )
    except PermissionError as denied:
        return {"denied": str(denied)}
    return {"args": args}


def execute_core(self, tool, args, key, server="native"):
    if tool in self.config.get("deny_tools", []):
        raise PermissionError(f"Remote execution policy denies {tool}")
    if server != "native":
        return self.client(server).call("tools/call", {"name": tool, "arguments": args})
    if tool not in NATIVE_TOOLS:
        raise ValueError(f"Unsupported remote tool: {tool}")
    if tool == "Bash":
        return self.bash(args, key)
    if tool == "TaskStop":
        record = self.stop_command(args["task_id"])
        return {
            "message": "Remote process group stopped",
            "task_id": args["task_id"],
            "task_type": "local_bash",
            "command": record["command"],
        }
    args = {
        key: native_path(value)
        if key in ("file_path", "notebook_path", "path") and isinstance(value, str)
        else value
        for key, value in args.items()
    }
    result = self.client("native").call("tools/call", {"name": tool, "arguments": args})
    if result.get("isError"):
        raise RuntimeError(result["content"][0]["text"])
    return json.loads(result["content"][0]["text"])


def control(self, params):
    kind = params["subtype"]
    if kind == "checkpoint":
        self.hooks("Stop", "", {}, params["request_id"])
        # The platform's own command, not a call of the agent's: the
        # project's tool hooks never see it, as they never would natively.
        args: dict = {"command": "cheese-sync"}
        turn = params.get("turn")
        if turn:
            # A sync's failure is reported with this turn, so the note lands
            # in that turn's process view rather than being guessed later.
            # `platform_env` is what names it to the command; `invoke` drops
            # that key from any call the platform did not make.
            args["platform_env"] = {"CHEESE_TURN": str(turn)}
        return self.invoke(
            {
                "id": params["request_id"],
                "tool": "Bash",
                "args": args,
                "platform": True,
            }
        )
    if kind == "shell":
        return self.shell(params)
    if kind in ("files", "git"):
        return beside(f"machine_{kind}")["answer"](params, str(self.root))
    if kind == "background":
        # The named Bash call returns now, its command running on. Named by
        # the call rather than by what is waiting: a call that is only
        # starting has not registered yet, and must find the request when
        # it does. Another session's calls on this machine are not touched.
        with self.command_lock:
            self.backgrounded.add(params["id"])
            return {"background": self.foreground.get(params["id"])}
    if kind == "tool_hooks":
        return self.tool_hooks(params)
    if kind == "read_file":
        path = Path(native_path(params["path"]))
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


def prepare(self, payload):
    # The state is <owner>/<root>/home/<project>/<resource>/.cheese/executor,
    # and the root is the enrollment's (`.cheese`, or `.cheese/profiles/<name>`).
    root = os.environ.get("CHEESE_PROFILE_ROOT", ".cheese")
    room_owner = self.state.parents[4 + len(Path(root).parts)]
    home = (
        room_owner
        / root
        / "home"
        / str(uuid.UUID(payload["project"]))
        / str(uuid.UUID(payload["resource"]))
    )
    if home / ".cheese/executor" != self.state:
        raise ValueError("Executor preparation belongs to another room")
    bootstrap = runpy.run_path(str(self.programs / "remote-execution/bootstrap.py"))
    disk = {}
    with bootstrap["prepared"](
        payload,
        room_owner,
        disk=disk,
        # Read-only in a sandbox: only the install, outside it, fetches it.
        fetch_toolchain=not self.config.get("sandbox"),
    ) as (
        _,
        config,
        _,
        _,
    ):
        # The two keys a configure replaces, and so the two the running
        # room is allowed to differ in: everything else has to match.
        configure = {"env": config["env"], "env_reset": config.get("env_reset", {})}
        if {k: v for k, v in self.config.items() if k not in configure} != {
            k: v for k, v in config.items() if k not in configure
        }:
            raise RuntimeError(
                "Executor configuration changed; restart the room environment"
            )
        info = {
            **self.dispatch("configure", configure),
            "mcp_servers": bootstrap["process_servers"](config),
            "context_tree": self.context_fs({"operation": "tree"}),
            **disk,
        }
        if payload.get("environment"):
            runner = runpy.run_path(str(self.programs / "cheese-environment.py"))
            info["environment_status"] = runner["read_status"](
                home / ".cheese-environment"
            )["state"]
        return info


def close(self):
    for command_id in list(self.running):
        self.stop_command(command_id)
    for client in self.clients.values():
        client.close()
    if getattr(self, "forge_bridge", None) is not None:
        with contextlib.suppress(Exception):
            self.forge_bridge.shutdown()
            self.forge_bridge.server_close()
    if self.cli_worker is not None:
        assert self.cli_worker.stdin is not None
        self.cli_worker.stdin.close()
        self.cli_worker.wait(timeout=10)
