"""Central session configuration and transport to an assigned executor."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

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


class RemoteClient:
    def __init__(self, config):
        self.config = config

    def command(self, mode, server=None):
        command = [*self.config["command"], mode, "--state", self.config["state"]]
        if server:
            command.extend(["--server", server])
        # SSH joins all arguments after the host with spaces. Quote the remote
        # command once, rather than letting workspace names become shell syntax.
        if self.config.get("ssh"):
            command = [
                "ssh",
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=10",
                self.config["ssh"],
                shlex.join(command),
            ]
        return command

    def call(self, method, params=None):
        result = subprocess.run(
            self.command("request"),
            input=json.dumps({"method": method, "params": params or {}}),
            text=True,
            capture_output=True,
            timeout=660,
        )
        if result.returncode:
            raise RuntimeError(
                "Remote executor request failed: " + result.stderr.strip()
            )
        return json.loads(result.stdout)

    def control(self, request):
        request = dict(request)
        if "path" in request:
            request["path"] = self.remote_path(request["path"])
        return self.call("control", request)

    def remote_path(self, path):
        center = self.config.get("central_workspace", "")
        if center and (path == center or path.startswith(center + "/")):
            return self.config["workspace"] + path[len(center) :]
        return path


def prepare(
    directory,
    target,
    *,
    claude="claude",
    extra_args=(),
    base_settings=None,
    home_override=None,
    config_override=None,
):
    version = subprocess.check_output([claude, "--version"], text=True).split()[0]
    if version != PINNED_VERSION:
        raise RuntimeError(
            f"Remote execution requires Claude Code {PINNED_VERSION}; found {version}"
        )
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    workspace = directory / "workspace"
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
    hooks = settings.setdefault("hooks", {})
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
                        "command": shlex.join([*helper, "context", str(target_path)]),
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
    servers = {}
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
    prefix.write_text(
        "#!/bin/sh\nexec "
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
    launch = {
        "command": command,
        "env": env,
        "cwd": str(workspace),
        "execution": str(target_path),
    }
    (directory / "launch.json").write_text(json.dumps(launch))
    return launch


def sync_context(target_path):
    target = json.loads(Path(target_path).read_text())
    snapshot = RemoteClient(target).call("context")
    workspace = Path(target["central_workspace"])
    manifest = Path(target_path).parent / "context-manifest.json"
    old = json.loads(manifest.read_text()) if manifest.exists() else []
    files = snapshot["files"]
    for name in old:
        if name not in files:
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
    manifest.write_text(json.dumps(list(files)))
    config = Path(target["central_config"])
    (config / "CLAUDE.md").write_text(snapshot["instructions"])
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
        and words[2] in ("bridge", "guard", "context", "event", "invoke")
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


def publish_event(config, payload):
    output = {}
    for group in config.get("central_hooks", {}).get(payload["hook_event_name"], []):
        matcher = group.get("matcher", "*")
        if matcher not in ("*", "") and not re.fullmatch(matcher, payload["tool_name"]):
            continue
        for hook in group.get("hooks", []):
            if hook["type"] != "command":
                raise ValueError("Execution event forwarding requires command hooks")
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
            "event",
            "invoke",
        ],
    )
    parser.add_argument("config", type=Path)
    parser.add_argument("args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if args.mode == "bridge":
        command = RemoteClient(config).command("bridge", args.args[0])
        os.execvp(command[0], command)
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
    elif args.mode == "event":
        print(json.dumps(publish_event(config, json.load(sys.stdin))))
    elif args.mode == "invoke":
        payload = json.load(sys.stdin)
        client = RemoteClient(config)

        def cancel_invoke(signum, _frame):
            if payload["tool"] == "Bash":
                client.control({"subtype": "stop_request", "request_id": payload["id"]})
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, cancel_invoke)
        signal.signal(signal.SIGINT, cancel_invoke)
        print(json.dumps(client.call("invoke", payload)))
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
