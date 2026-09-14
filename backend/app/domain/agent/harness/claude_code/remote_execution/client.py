"""Central session configuration and transport to an assigned executor."""

from __future__ import annotations

import sys

# Prompt hooks can use the existing MCP process before loading HTTP and CLI
# dependencies. Initial startup still synchronizes directly when it is absent.
if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "context":
    from context_service import call as call_context

    if call_context(sys.argv[2]):
        raise SystemExit(0)

import argparse
import json
import os
import re
import shlex
import signal
import subprocess
import time
import uuid
from pathlib import Path

if __package__:
    from app.domain.agent.executor_transport import RemoteClient
else:
    # Source scripts find the shared module in agent/; deployed bundles ship
    # the same module beside this script, which remains first on sys.path.
    sys.path.append(str(Path(__file__).resolve().parents[3]))
    from executor_transport import RemoteClient

PINNED_VERSION = "2.1.265"
NATIVE_TOOLS = (
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Glob",
    "Grep",
    "NotebookEdit",
    "TaskOutput",
    "TaskStop",
)
REMOTE_CONTROLS = {
    "read_file",
    "file_suggestions",
    "get_workspace_diff",
    "background_tasks",
    "stop_task",
}
PRIVATE_INSTRUCTIONS = (
    "This chat has 64 MiB of temporary scratch space at /work. "
    "Use shell and file tools for drafts and small processing tasks. "
    "Save finished documents through cheese doc set and publish artifacts "
    "through cheese artifact. Scratch files can disappear when execution "
    "is released; they are not permanent storage. No project checkout is mounted."
)


def prepare(
    directory,
    target,
    *,
    claude="claude",
    extra_args=(),
    base_settings=None,
    home_override=None,
    config_override=None,
    workspace_override=None,
):
    version = subprocess.check_output([claude, "--version"], text=True).split()[0]
    if version != PINNED_VERSION:
        raise RuntimeError(
            f"Remote execution requires Claude Code {PINNED_VERSION}; found {version}"
        )
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.get("kind") == "private":
        if __package__:
            from .private import ensure
        else:
            from private import ensure

        ensure(target, directory, os.environ)
    workspace = (
        Path(workspace_override) if workspace_override else directory / "workspace"
    )
    workspace.mkdir(exist_ok=True)
    # Stop native project discovery at this generated mirror's boundary.
    if not (workspace / ".git").exists():
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    home = Path(home_override) if home_override else directory / "home"
    home.mkdir(exist_ok=True)
    config = Path(config_override) if config_override else directory / "config"
    config.mkdir(exist_ok=True)
    client = RemoteClient(target)
    info = client.call("ping")
    if target.get("kind") == "private":
        info["workspace"] = "/work"
    target = dict(
        target,
        workspace=info["workspace"],
        central_workspace=str(workspace),
        central_config=str(config),
        helper=[sys.executable, str(Path(__file__).resolve())],
        central_hooks=(base_settings or {}).get("hooks", {}),
        target_file=str(directory / "execution.json"),
    )
    target_path = directory / "execution.json"
    target_path.write_text(json.dumps(target))
    target_path.chmod(0o600)
    sync_context(target_path)
    plugin = directory / "plugin"
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin / "hooks").mkdir(exist_ok=True)
    (plugin / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "cheese-remote-execution", "version": "0.1.0"})
    )
    (plugin / "hooks/hooks.json").write_text('{"modules":["proxy.js"]}')
    module = (Path(__file__).parent / "proxy.js").read_text()
    module = module.replace("__EXECUTION_CONFIG__", json.dumps(target))
    (plugin / "hooks/proxy.js").write_text(module)
    settings = json.loads(json.dumps(base_settings or {}))
    permissions = settings.setdefault("permissions", {})
    allowed = permissions.setdefault("allow", [])
    for tool in ("invoke", "chat_send", "platform_request", "cheese_*"):
        if f"mcp__native__{tool}" not in allowed:
            allowed.append(f"mcp__native__{tool}")
    hooks = settings.setdefault("hooks", {})
    # The transport publishes hooks for the original tool. Running them again
    # for its internal MCP call duplicates events and delays both directions.
    for event in ("PreToolUse", "PostToolUse"):
        for group in hooks.get(event, []):
            matcher = group.get("matcher", "*")
            matcher = ".*" if matcher in ("*", "") else matcher
            group["matcher"] = (
                f"^(?!mcp__native__(?:invoke|chat_send|platform_request|cheese_.*)$).*(?:{matcher})"
            )
    helper = [sys.executable, str(Path(__file__).resolve())]
    guard = shlex.join([*helper, "guard", str(target_path)])
    hooks.setdefault("PreToolUse", []).insert(
        0,
        {
            "matcher": "|".join((*NATIVE_TOOLS, "EnterWorktree", "ExitWorktree")),
            "hooks": [{"type": "command", "command": guard}],
        },
    )
    for event in ("SessionStart", "UserPromptSubmit"):
        hooks.setdefault(event, []).insert(
            0,
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": helper[0],
                        "args": [
                            str(
                                Path(__file__).with_name("context_service.py").resolve()
                            ),
                            str(target_path),
                        ],
                    }
                ]
            },
        )
    if target.get("kind") == "device":
        hooks.setdefault("Stop", []).insert(
            0,
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": shlex.join(
                            [*helper, "checkpoint", str(target_path)]
                        ),
                        "timeout": 660,
                    }
                ]
            },
        )
    settings.update(
        skipDangerousModePermissionPrompt=True,
        enableArtifact=False,
        attribution={"sessionUrl": False},
    )
    (config / "settings.json").write_text(json.dumps(settings))
    gates = {
        "hasCompletedOnboarding": True,
        "autoUpdates": False,
        "bypassPermissionsModeAccepted": True,
        "projects": {
            str(workspace): {
                "hasTrustDialogAccepted": True,
                "hasCompletedProjectOnboarding": True,
            }
        },
    }
    gate_file = config / ".claude.json"
    previous = json.loads(gate_file.read_text()) if gate_file.exists() else {}
    gate_file.write_text(json.dumps({**previous, **gates}))
    servers = {
        "native": {
            "type": "stdio",
            "command": helper[0],
            "args": [*helper[1:], "transport", str(target_path)],
        }
    }
    for name in target.get("mcp_servers", []):
        if name == "native":
            raise ValueError("MCP server name native is reserved for file operations")
        servers[name] = {
            "type": "stdio",
            "command": helper[0],
            "args": [*helper[1:], "bridge", str(target_path), name],
        }
    (directory / "mcp.json").write_text(json.dumps({"mcpServers": servers}))
    env = {
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config),
        "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1",
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
        "ENABLE_TOOL_SEARCH": "false",
        "CHEESE_EXECUTION_CONFIG": str(target_path),
    }
    prefix = directory / "shell-prefix"
    local_commands = {
        hook["command"]
        for groups in target["central_hooks"].values()
        for group in groups
        for hook in group.get("hooks", [])
        if hook.get("type") == "command"
    }
    local_commands.update(
        shlex.join([*helper, mode, str(target_path)])
        for mode in ("guard", "context", "checkpoint", "transport")
    )
    local_commands.update(
        shlex.join([*helper, "bridge", str(target_path), name])
        for name in target.get("mcp_servers", [])
    )
    # Match complete trusted commands; appended shell syntax takes the usual route.
    dispatch = (
        'case "$1" in\n'
        + "".join(
            f'  {shlex.quote(command)}) exec sh -c "$1" ;;\n'
            for command in sorted(local_commands)
        )
        + "esac\n"
    )
    prefix.write_text(
        "#!/bin/sh\n"
        + dispatch
        + "exec "
        + shlex.join([*helper, "shell", str(target_path)])
        + ' "$@"\n'
    )
    prefix.chmod(0o700)
    env["CLAUDE_CODE_SHELL_PREFIX"] = str(prefix)
    command = [
        claude,
        "--setting-sources",
        "user",
        "--plugin-dir",
        str(plugin),
        "--strict-mcp-config",
        "--mcp-config",
        str(directory / "mcp.json"),
        "--disallowedTools",
        "EnterWorktree,ExitWorktree",
        "--no-chrome",
        *extra_args,
    ]
    if target.get("kind") == "private":
        # Subagents and arbitrary plugins must not create another local execution
        # route around the proxy. Skills shipped by the platform remain available.
        command.extend(
            [
                "--disallowedTools",
                "Agent,Task,WebFetch,WebSearch,EnterWorktree,ExitWorktree",
            ]
        )
    launch = {
        "command": command,
        "env": env,
        "cwd": str(workspace),
        "execution": str(target_path),
    }
    (directory / "launch.json").write_text(json.dumps(launch))
    return launch


def sync_context(target_path):
    import base64
    import hashlib

    target = json.loads(Path(target_path).read_text())
    workspace = Path(target["central_workspace"])
    manifest = Path(target_path).parent / "context-manifest.json"
    old = json.loads(manifest.read_text()) if manifest.exists() else []
    known_files = {}
    for name in old:
        path = workspace / name
        if path.is_file():
            known_files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    # The shell can replace even the executor's own files and responses. Keep
    # executable central configuration independent of anything it returns.
    snapshot = (
        {"files": {}, "instructions": PRIVATE_INSTRUCTIONS}
        if target.get("kind") == "private"
        else RemoteClient(target).call("context", {"known_files": known_files})
    )
    files = snapshot["files"]
    file_names = snapshot.get("file_names", list(files))
    for name in old:
        if name not in file_names:
            (workspace / name).unlink(missing_ok=True)
    for name, encoded in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid remote context path")
        # Project settings can contain executable hooks. The executor owns those;
        # importing them here would run project code on the central host.
        if name in (".claude/settings.json", ".claude/settings.local.json"):
            continue
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
    if old != file_names:
        manifest.write_text(json.dumps(file_names))
    config = Path(target["central_config"])
    instructions = config / "CLAUDE.md"
    if (
        not instructions.exists()
        or instructions.read_text() != snapshot["instructions"]
    ):
        instructions.write_text(snapshot["instructions"])
    for name in ("skills", "commands", "agents", "rules"):
        source = workspace / ".claude" / name
        link = config / name
        if source.exists():
            if not link.exists():
                link.symlink_to(source, target_is_directory=True)
            elif not link.is_symlink():
                for child in source.iterdir():
                    destination = link / child.name
                    if not destination.exists():
                        destination.symlink_to(
                            child, target_is_directory=child.is_dir()
                        )
    return snapshot


def shell(target_path, command):
    target = json.loads(Path(target_path).read_text())
    words = shlex.split(command)
    helper = str(Path(__file__).resolve())
    # Only the platform's own transport and guard run on the central host.
    if (
        len(words) >= 4
        and words[:2] == [sys.executable, helper]
        and words[2] in ("bridge", "guard", "context", "checkpoint", "transport")
        and words[3] == str(target_path)
    ):
        os.execvp(words[0], words)
    commands = {
        hook["command"]
        for groups in target.get("central_hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
        if hook.get("type") == "command"
    }
    if command in commands:
        os.execvp("sh", ["sh", "-c", command])
    local_chat = _local_chat_send_argv(command)
    if local_chat is not None:
        result = _publish_chat_locally(local_chat)
        if result is not None:
            return result
    client = RemoteClient(target)
    command = command.replace(
        target["central_config"] + "/skills/", target["workspace"] + "/.claude/skills/"
    )
    command = command.replace(target["central_workspace"], target["workspace"])
    request_id = "shell-" + uuid.uuid4().hex

    def cancel(signum, _frame):
        client.control({"subtype": "stop_request", "request_id": request_id})
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, cancel)
    signal.signal(signal.SIGINT, cancel)
    result = client.call(
        "invoke",
        {
            "id": request_id,
            "tool": "Bash",
            "args": {"command": command, "run_in_background": True},
        },
    )
    if "error" in result:
        raise RuntimeError(result["error"])
    task_id = result["value"]["backgroundTaskId"]
    while True:
        task = client.control({"subtype": "task_output", "task_id": task_id})
        if task["status"] != "running":
            sys.stdout.write(task["stdout"])
            sys.stderr.write(task["stderr"])
            return task["exit_code"] if task["exit_code"] is not None else 1
        time.sleep(0.1)


def _local_chat_send_argv(command):
    """Return argv for the safe, direct platform publication fast path.

    ``cheese chat send`` is a platform action whose credentials are already in
    the central session environment. Only a standalone invocation is eligible;
    shell operators and expansions stay on the remote executor so this cannot
    turn an appended command into a central-host escape.
    """
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        words = list(lexer)
    except ValueError:
        return None
    if len(words) < 3 or words[:3] != ["cheese", "chat", "send"]:
        return None
    if any(token in {";", "&&", "||", "|", ">", ">>", "<", "<<"} for token in words):
        return None
    # These expansions are meaningful only to a shell. Running the CLI
    # directly must preserve quoted message text and never reinterpret them.
    quote = None
    escaped = False
    for _, char in enumerate(command):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote is None and char in "'\"":
            quote = char
            continue
        if quote is not None and char == quote:
            quote = None
            continue
        if quote is None and (char in ";&|<>`\n$" or char == "\r"):
            return None
    if quote is not None or escaped:
        return None
    return words


def _publish_chat_locally(argv):
    """Publish an inline chat message using the session's scoped credentials.

    File-backed messages remain remote because their path belongs to the
    executor workspace. Returning ``None`` asks ``shell`` to use its normal
    remote path for unsupported or incomplete local inputs.
    """
    if "--help" in argv or "--file" in argv:
        return None
    content = None
    reply_to = None
    request_id = None
    index = 3
    while index < len(argv):
        value = argv[index]
        if value in ("--reply-to", "--request-id"):
            if index + 1 >= len(argv):
                return None
            if value == "--reply-to":
                reply_to = argv[index + 1]
            else:
                request_id = argv[index + 1]
            index += 2
            continue
        if value.startswith("-") or content is not None:
            return None
        content = value
        index += 1
    api = os.environ.get("CHEESE_API", "").rstrip("/")
    token = os.environ.get("CHEESE_TOKEN", "")
    topic = os.environ.get("CHEESE_TOPIC", "")
    if not content or not content.strip() or not api or not token or not topic:
        return None
    try:
        publication_id = str(uuid.UUID(request_id)) if request_id else str(uuid.uuid4())
    except (ValueError, AttributeError):
        return None
    body = {"content": content, "request_id": publication_id}
    if reply_to:
        body["reply_to"] = reply_to
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{api}/topics/{topic}/messages",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Cheese-Token": token,
            **(
                {"X-Cheese-Turn": os.environ["CHEESE_TURN"]}
                if os.environ.get("CHEESE_TURN")
                else {}
            ),
        },
    )
    print(
        f"[cheese] request_id={publication_id}；重试请带 --request-id {publication_id}",
        file=sys.stderr,
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            result = json.load(response)
    except (urllib.error.HTTPError, urllib.error.URLError) as exc:
        print(f"[cheese] POST /topics/{topic}/messages 出错: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result["data"], ensure_ascii=False))
    return 0


def _publish_spooled_hook(command, payload):
    if (
        command != "cheese-hook"
        or payload["hook_event_name"] not in ("PreToolUse", "PostToolUse")
        or not os.environ.get("CHEESE_HOOK_SPOOL_ONLY")
        or not os.environ.get("CHEESE_HOOK_SPOOL")
    ):
        return False
    import shutil

    executable = shutil.which(command)
    if executable is None:
        return False
    if __package__:
        from ..event_spool import append
        from ..hooks_substrate import CHEESE_HOOK_SCRIPT

        expected = CHEESE_HOOK_SCRIPT.encode()
    else:
        from event_spool import append

        expected = Path(__file__).with_name("platform-hook-source").read_bytes()
    try:
        actual = Path(executable).read_bytes()
    except OSError:
        return False
    if actual != expected:
        return False
    try:
        spool = Path(os.environ["CHEESE_HOOK_SPOOL"])
        spool.mkdir(parents=True, exist_ok=True)
        try:
            spool.chmod(0o777)
        except OSError:
            pass
        append(spool, str(uuid.uuid4()), payload)
    except OSError:
        # The managed shell hook is best effort and never denies a tool on IO failure.
        pass
    return True


def publish_event(config, payload):
    output = {}
    for group in config.get("central_hooks", {}).get(payload["hook_event_name"], []):
        matcher = group.get("matcher", "*")
        if matcher not in ("*", "") and not re.fullmatch(matcher, payload["tool_name"]):
            continue
        for hook in group.get("hooks", []):
            if hook["type"] != "command":
                raise ValueError("Execution event forwarding requires command hooks")
            if _publish_spooled_hook(hook["command"], payload):
                continue
            result = subprocess.run(
                ["sh", "-c", hook["command"]],
                input=json.dumps(payload),
                text=True,
                capture_output=True,
                timeout=hook.get("timeout", 60),
            )
            if result.returncode:
                raise RuntimeError(result.stderr)
            if result.stdout.strip():
                output = json.loads(result.stdout)
                decision = output.get("hookSpecificOutput", {})
                if decision.get("permissionDecision") == "deny":
                    return {
                        "deny": decision.get(
                            "permissionDecisionReason",
                            "Central policy denied operation",
                        )
                    }
    return output


def transport(config, target_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    if __package__:
        from .context_service import serve
    else:
        from context_service import serve

    client = RemoteClient(config)
    output_lock = threading.Lock()
    active = {}
    active_lock = threading.RLock()
    cancelled = set()

    def cancel(request_id):
        with active_lock:
            payload = active.get(request_id)
            if payload is None:
                return
            cancelled.add(request_id)
        if payload.get("tool") == "Bash":
            client.control({"subtype": "stop_request", "request_id": payload["id"]})

    def stop(signum, _frame):
        with active_lock:
            requests = list(active)
        for request_id in requests:
            cancel(request_id)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def handle(request):
        try:
            method = request["method"]
            if method == "initialize":
                value = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "cheese-native-execution", "version": "1"},
                }
            elif method == "tools/list":
                capabilities = client.call("ping", {}).get("capabilities", [])
                cli_tools = (
                    client.call("cli", {"method": "tools/list"})["tools"]
                    if "cli_worker" in capabilities
                    else []
                )
                value = {
                    "tools": [
                        {
                            "name": "invoke",
                            "description": (
                                "Internal native tool transport. "
                                "Use the native file and shell tools."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "tool": {
                                        "type": "string",
                                        "enum": list(NATIVE_TOOLS),
                                    },
                                    "args": {"type": "object"},
                                    "session_id": {"type": "string"},
                                },
                                "required": ["id", "tool", "args", "session_id"],
                            },
                        },
                        {
                            "name": "chat_send",
                            "description": (
                                "Publish a message to the current Cheese room. "
                                "Use for user-visible updates and replies; "
                                "ordinary model output is not published."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "content": {"type": "string"},
                                    "reply_to": {"type": "string"},
                                    "request_id": {
                                        "type": "string",
                                        "description": (
                                            "UUID from an uncertain prior result; "
                                            "reuse with the same content to retry."
                                        ),
                                    },
                                },
                                "required": ["content"],
                            },
                        },
                        {
                            "name": "platform_request",
                            "description": (
                                "Call the Cheese backend with room credentials. "
                                "Use for platform documents, tasks and metadata. "
                                "Paths are relative to the API root. "
                                "Use chat_send for messages and native tools "
                                "for project files."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "method": {
                                        "type": "string",
                                        "enum": [
                                            "GET",
                                            "POST",
                                            "PUT",
                                            "PATCH",
                                            "DELETE",
                                        ],
                                    },
                                    "path": {"type": "string"},
                                    "body": {
                                        "description": (
                                            "JSON body, without shell parsing."
                                        )
                                    },
                                },
                                "required": ["method", "path"],
                            },
                        },
                    ]
                    + cli_tools
                }
            elif method == "tools/call":
                tool = request["params"]["name"]
                if tool not in (
                    "invoke",
                    "chat_send",
                    "platform_request",
                ) and not tool.startswith("cheese_"):
                    raise ValueError("Unknown transport tool")
                payload = request["params"]["arguments"]
                if tool == "chat_send":
                    payload = {
                        "id": payload["id"],
                        "session_id": payload["session_id"],
                        "tool": "mcp__native__chat_send",
                        "args": {
                            key: payload[key]
                            for key in ("content", "reply_to", "request_id")
                            if key in payload
                        },
                    }
                elif tool == "platform_request":
                    payload = {
                        "id": payload["id"],
                        "session_id": payload["session_id"],
                        "tool": "mcp__native__platform_request",
                        "args": {
                            key: payload[key]
                            for key in ("method", "path", "body")
                            if key in payload
                        },
                    }
                elif tool.startswith("cheese_"):
                    payload = {
                        "id": payload["id"],
                        "session_id": payload["session_id"],
                        "tool": "mcp__native__" + tool,
                        "args": {
                            key: value
                            for key, value in payload.items()
                            if key not in ("id", "session_id")
                        },
                    }
                with active_lock:
                    if request["id"] in cancelled:
                        raise RuntimeError("Tool call was cancelled")
                if tool == "invoke" and payload["tool"] not in NATIVE_TOOLS:
                    raise ValueError("Unknown native tool")
                event = {
                    "hook_event_name": "PreToolUse",
                    "session_id": payload["session_id"],
                    "tool_name": payload["tool"],
                    "tool_use_id": payload["id"],
                    "tool_input": payload["args"],
                    "cwd": config["workspace"],
                }
                decision = publish_event(config, event)
                if decision.get("deny"):
                    outcome = decision
                else:
                    args = decision.get("hookSpecificOutput", {}).get(
                        "updatedInput", payload["args"]
                    )
                    receipt = (
                        client.platform_request(args)
                        if tool == "platform_request"
                        else client.publish_message(payload, args)
                        if tool == "chat_send"
                        else client.publish_chat(payload, args)
                    )
                    if tool.startswith("cheese_"):
                        receipt = client.call(
                            "invoke",
                            {
                                "id": payload["id"],
                                "tool": payload["tool"],
                                "args": args,
                            },
                        )
                    if receipt is None:
                        receipt = client.call(
                            "invoke",
                            {
                                "id": payload["id"],
                                "tool": payload["tool"],
                                "args": args,
                            },
                        )
                    if "error" in receipt:
                        outcome = {"deny": receipt["error"]}
                    else:
                        publish_event(
                            config,
                            dict(
                                event,
                                hook_event_name="PostToolUse",
                                tool_input=args,
                                tool_response=receipt["value"],
                            ),
                        )
                        outcome = {"result": receipt["value"]}
                value = {"content": [{"type": "text", "text": json.dumps(outcome)}]}
            elif method == "ping":
                value = {}
            else:
                raise ValueError("Unknown transport method")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": value}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32000, "message": str(exc)},
            }
        with output_lock:
            print(json.dumps(response), flush=True)
        with active_lock:
            active.pop(request["id"], None)
            cancelled.discard(request["id"])

    with (
        serve(target_path, lambda: sync_context(target_path)),
        ThreadPoolExecutor() as workers,
    ):
        for line in sys.stdin:
            request = json.loads(line)
            if request.get("method") == "notifications/cancelled":
                workers.submit(cancel, request["params"]["requestId"])
            if "id" in request:
                if request.get("method") == "tools/call":
                    with active_lock:
                        active[request["id"]] = request["params"].get("arguments", {})
                workers.submit(handle, request)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "prepare",
            "launch",
            "bridge",
            "guard",
            "context",
            "control",
            "shell",
            "bootstrap",
            "checkpoint",
            "release",
            "transport",
        ],
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.mode == "transport":
        transport(config, args.config)
    elif args.mode == "release":
        if __package__:
            from .private import release
        else:
            from private import release

        release(config)
    elif args.mode == "bridge":
        if config.get("kind") == "device":
            if __package__:
                from .runtime import bridge
            else:
                from runtime import bridge

            bridge(None, args.args[0], call=RemoteClient(config).call)
        else:
            command = RemoteClient(config).command("bridge", args.args[0])
            os.execvp(command[0], command)
    elif args.mode == "checkpoint":
        import hashlib

        payload = json.load(sys.stdin)
        transcript = Path(payload["transcript_path"])
        identifier = hashlib.sha256(
            (
                json.dumps(payload, sort_keys=True) + str(transcript.stat().st_size)
            ).encode()
        ).hexdigest()
        result = RemoteClient(config).control(
            {
                "subtype": "checkpoint",
                "request_id": "checkpoint-" + identifier,
            }
        )
        if "error" in result:
            raise RuntimeError(result["error"])
    elif args.mode == "guard":
        json.load(sys.stdin)
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": "Central execution is disabled; "
                        "the remote execution plugin did not handle this call",
                    }
                }
            )
        )
    elif args.mode == "shell":
        raise SystemExit(shell(args.config, args.args[0]))
    elif args.mode == "bootstrap":
        base_dir = args.config.parent
        launch = prepare(
            base_dir / "remote-session",
            config,
            claude=args.args[0],
            extra_args=args.args[1:],
            base_settings=json.loads((base_dir / "settings.json").read_text()),
            home_override=os.environ["HOME"],
            config_override=base_dir,
            workspace_override=os.environ["CHEESE_WORK"],
        )
        os.chdir(launch["cwd"])
        os.execvpe(
            launch["command"][0], launch["command"], dict(os.environ, **launch["env"])
        )
    elif args.mode == "context":
        sync_context(args.config)
    elif args.mode == "control":
        print(json.dumps(RemoteClient(config).control(json.load(sys.stdin))))
    elif args.mode == "prepare":
        print(json.dumps(prepare(args.args[0], config, extra_args=args.args[1:])))
    else:
        env = dict(os.environ, **config["env"])
        os.chdir(config["cwd"])
        os.execvpe(config["command"][0], config["command"], env)


if __name__ == "__main__":
    main()
