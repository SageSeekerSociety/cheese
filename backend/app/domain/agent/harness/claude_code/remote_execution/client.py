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
import base64
import contextlib
import ctypes
import functools
import hashlib
import io
import json
import logging
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any

if __package__:
    from app.domain.agent.executor_transport import (
        DEFERRED_WORKSPACE,
        MACHINE_OUT_OF_REACH,
        SESSION_SOCKET,
        MachineOutOfReach,
        PlatformHost,
        RemoteClient,
        dial_runner,
        read_file_on_the_machine,
        session_path,
        session_servers,
        stat_file_on_the_machine,
    )
    from app.domain.agent.harness.claude_code.remote_execution.context_service import (
        serve,
    )
    from app.domain.agent.harness.claude_code.remote_execution.release import (
        allow_native_tools,
        carry_transcripts,
        forget_touched_skills,
        hook_module,
        link_forwarded_user_context,
        platform_tool_names,
        touch_skills,
    )
    from app.domain.agent.harness.claude_code.remote_execution.runtime import bridge
    from app.domain.agent.harness.claude_code.remote_execution.shell_stop import (
        CAUGHT,
        KILL,
        current_target,
        deliver_stop,
        start_watcher,
    )
else:
    # Source scripts find the shared module in agent/; deployed bundles ship
    # the same module beside this script, which remains first on sys.path.
    sys.path.append(str(Path(__file__).resolve().parents[3]))
    from executor_transport import (
        DEFERRED_WORKSPACE,
        MACHINE_OUT_OF_REACH,
        SESSION_SOCKET,
        MachineOutOfReach,
        PlatformHost,
        RemoteClient,
        dial_runner,
        read_file_on_the_machine,
        session_path,
        session_servers,
        stat_file_on_the_machine,
    )

    # Its siblings sit beside it, which a script run has first on sys.path and
    # a file loaded by path has not.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from context_service import serve
    from release import (
        allow_native_tools,
        carry_transcripts,
        forget_touched_skills,
        hook_module,
        link_forwarded_user_context,
        platform_tool_names,
        touch_skills,
    )
    from runtime import bridge
    from shell_stop import CAUGHT, KILL, current_target, deliver_stop, start_watcher

PINNED_VERSION = "2.1.282"
# Where a session's execution target is written. The launcher names it, so the
# runner that holds the session and `bootstrap` preparing it agree on the file
# without either spelling the other's layout.
EXECUTION_CONFIG = "CHEESE_EXECUTION_CONFIG"
# The file tools the plugin runs on the executor. Bash is not one: the build
# runs it itself, through the shell prefix (`shell`), so its tasks, their
# controls and their notifications are the build's own.
NATIVE_TOOLS = (
    "Read",
    "Edit",
    "Write",
    "NotebookEdit",
)
# What the build adds to its shell children's environment, forwarded to a
# command on the executor because a native session there would set the same.
# Nothing else of this host's environment leaves it — its credentials are in
# there — and the build's messaging socket and its token name a process on
# this host, so they stay too.
FORWARDED_ENV = (
    "AI_AGENT",
    "CLAUDECODE",
    "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ID",
    "CLAUDE_CODE_SESSION_ATTENDED",
    "COREPACK_ENABLE_AUTO_PIN",
    "GIT_EDITOR",
    "NoDefaultCurrentDirectoryInExePath",
)
# The build wraps each Bash command as
# `source <snapshot> 2>/dev/null || true && <options> && eval '<command>'
# < /dev/null && pwd -P >| <tmp>/claude-<hex>-cwd`. The snapshot is of this
# host's shell and the cwd file is where the build learns the new directory,
# so the executor swaps in its own snapshot and file, and the prefix copies
# the directory back.
_SNAPSHOT = re.compile(r"source ('(?:[^']|'\\'')*'|\S+) 2>/dev/null \|\| true && ")
_CWD_FILE = re.compile(r" && pwd -P >\| ('(?:[^']|'\\'')*'|\S+)\Z")
# The stdin a hook command may carry to the executor, and the output a command
# may write onto this host (the build persists what it is handed).
SHELL_INPUT_BYTES = 16 * 1024 * 1024
SHELL_OUTPUT_BYTES = 256 * 1024 * 1024
# How long a command that never started keeps being retried across a dropped
# link before the machine is reported out of reach.
SHELL_START_RETRY_S = 60.0
# How long a reader keeps retrying an executor that answers, but with an error,
# before giving the command up.
SHELL_ERROR_RETRY_S = 60.0
SHELL_READ_RETRY_S = (0.5, 5.0)  # first and longest wait between read retries
PRIVATE_INSTRUCTIONS = (
    "This chat has 64 MiB of temporary scratch space at /work. "
    "Use shell and file tools for drafts and small processing tasks. "
    "Save finished documents through cheese_doc_set and publish artifacts "
    "through cheese show. Scratch files can disappear when execution "
    "is released; they are not permanent storage. No project checkout is mounted."
)


def _ensure_sync_agents_hook(hooks: dict) -> None:
    """发现层（session_launch.session_settings 的同款）：模型分身定义随会话启动
    和每个提示刷新。seed 的 settings.json 可能来自任一架构、任何年代，所以
    这里确定性地补一份（幂等），不指望 seed 够新。
    """
    command = "cheese sync-agents || true"
    for event in ("SessionStart", "UserPromptSubmit"):
        groups = hooks.setdefault(event, [])
        existing = [
            hook
            for group in groups
            for hook in group.get("hooks", [])
            if hook.get("command") in ("cheese sync-agents", command)
        ]
        if existing:
            # Older seed settings can still contain the prompt-blocking form.
            for hook in existing:
                hook["command"] = command
            continue
        groups.append(
            {"hooks": [{"type": "command", "command": command, "timeout": 15}]}
        )


def write_plugin(plugin: Path, target: dict, platform_tools: list, target_path) -> Path:
    """The plugin a room's session is started with: the function hook that runs
    its tools where the project is (`proxy.js`), and the Stop hook that keeps a
    turn from ending while a person in the room is unanswered (`reply`)."""
    (plugin / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin / "hooks").mkdir(exist_ok=True)
    (plugin / ".claude-plugin/plugin.json").write_text(
        json.dumps({"name": "cheese-remote-execution", "version": "0.1.0"})
    )
    stop = shlex.join(
        [sys.executable, str(Path(__file__).resolve()), "reply", str(target_path)]
    )
    (plugin / "hooks/hooks.json").write_text(
        json.dumps(
            {
                "modules": ["proxy.js"],
                "hooks": {"Stop": [{"hooks": [{"type": "command", "command": stop}]}]},
            }
        )
    )
    (plugin / "hooks/proxy.js").write_text(
        hook_module(
            (Path(__file__).parent / "proxy.js").read_text(), target, platform_tools
        )
    )
    return plugin


def reply_hook(payload: dict) -> dict | None:
    """The Stop hook: may this turn end? The runner decides (`driven/runner.py`
    `insist`), reached on the socket it gave this session. A session with no
    runner, or one that does not answer, ends as it would have."""
    path = os.environ.get(SESSION_SOCKET)
    if not path or payload.get("stop_hook_active"):
        return None
    try:
        with dial_runner(path, 10) as connection:
            connection.sendall(
                json.dumps({"method": "reply_check", "params": {}}).encode() + b"\n"
            )
            answer = json.loads(connection.makefile("rb").readline())
    except (OSError, ValueError):
        return None
    reason = (answer.get("result") or {}).get("reason")
    return {"decision": "block", "reason": reason} if reason else None


def cheese_source() -> Path:
    """The platform tool table: shipped beside this helper, or the checkout's."""
    shipped = Path(__file__).with_name("cheese.py")
    if shipped.is_file():
        return shipped
    return Path(__file__).resolve().parents[6] / "sandbox/cheese"


def prepare(
    directory,
    target: dict[str, Any],
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
        # Not lifted with the siblings above: `private` imports fcntl, which
        # Windows has not, and this runs on the member's own machine.
        if __package__:
            # deferred-import: private imports fcntl, which Windows has not
            from .private import ensure
        else:
            # deferred-import: private imports fcntl, which Windows has not
            from private import ensure

        ensure(target, directory, os.environ)
    if target.get("kind") == "deferred" and target["workspace"] != DEFERRED_WORKSPACE:
        target = _take_leased_machine(target)
    unavailable = target.get("kind") in {"unavailable", "deferred"}
    forwarded = target.get("kind") not in {"private", "unavailable"}
    device_forwarded = target.get("kind") in {"device", "deferred"}
    # The executor is on this machine (a member's own Claude Code on their own
    # computer, `owner_provider`): the session sees the project where the
    # executor holds it, with no forwarded view and no namespace of its own.
    # Before its lease it starts in an empty directory of its own, which is
    # its placeholder here.
    local = bool(target.get("local"))
    placeholder = str(directory / "workspace") if local else DEFERRED_WORKSPACE
    workspace = (
        directory / "workspace"
        if local
        else directory / "forwarded-project"
        if forwarded
        else Path(workspace_override)
        if workspace_override
        else directory / "workspace"
    )
    workspace.mkdir(exist_ok=True)
    if not forwarded and not (workspace / ".git").exists():
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    home = Path(home_override) if home_override else directory / "home"
    home.mkdir(exist_ok=True)
    config = Path(config_override) if config_override else directory / "config"
    config.mkdir(exist_ok=True)

    # What an earlier session process reached is not this one's: it starts
    # offering a subdirectory's skills only once it reaches them itself, and
    # the copies written for that are not skills the platform shipped.
    forget_touched_skills(directory, config)
    # The build's own temporary files — a Bash command's output, the file its
    # shell reports its directory in — kept to this session.
    temporary = directory / "tmp"
    temporary.mkdir(exist_ok=True, mode=0o700)
    client = RemoteClient(target)
    info = {"workspace": target["workspace"]} if unavailable else client.call("ping")
    # The credential the lease handed a session that took its machine here; it
    # is kept beside the target, never in it, which the plugin is given.
    execution_token = target.pop("execution_token", None)
    if target.get("kind") == "private":
        info["workspace"] = "/work"
    # Where the session sees the project: at the executor's own path, so that
    # every path the build prints is the one it prints running there (`enter`).
    # `central_workspace` is where this host holds the view of it.
    seen = session_path(info["workspace"])
    if local:
        seen = placeholder if unavailable else info["workspace"]
        if not unavailable:
            workspace = Path(info["workspace"])
    if seen != placeholder:
        carry_transcripts(config, placeholder, seen)
    target = dict(
        target,
        workspace=info["workspace"],
        session_workspace=seen,
        # Where its paths are rewritten from once it takes its machine
        # (`RemoteClient.call`): the directory it started in, not the
        # placeholder a session in its own namespace sees.
        **({"virtual_workspace": placeholder} if local and unavailable else {}),
        # The session reads every skill from its config dir. The project's
        # are links into the view, and are the project's on the executor; the
        # rest the platform wrote there itself, and the executor holds its own
        # copy in its own config dir. A file named beside a skill is sent to
        # whichever of the two holds it (`skill_path`).
        executor_config=session_path(info["config_dir"])
        if info.get("config_dir")
        else None,
        shipped_skills=sorted(
            entry.name
            for entry in (config / "skills").iterdir()
            if entry.is_dir() and not entry.is_symlink()
        )
        if (config / "skills").is_dir()
        else [],
        central_workspace=str(workspace),
        central_config=str(config),
        central_tmp=str(temporary),
        # The session's memory tree, in its own home on this host: the runner
        # lays the platform's copy down and collects the agent's edits there
        # (`runner.memory_root`, `memory.files.MEMORY_ROOT`), so its file tools
        # reach it here and not on the executor (`proxy.js` `memoryPath`).
        central_memory=str(home / ".cheese/memory"),
        helper=[sys.executable, str(Path(__file__).resolve())],
        central_hooks=(base_settings or {}).get("hooks", {}),
        target_file=str(directory / "execution.json"),
        **(
            {"token_file": str(directory / "execution.token")}
            if device_forwarded
            else {}
        ),
    )
    context_tree = target.pop("context_tree", None)
    target_path = directory / "execution.json"
    target_path.write_text(json.dumps(target))
    target_path.chmod(0o600)
    if device_forwarded:
        token_path = directory / "execution.token"
        token_path.write_text(execution_token or os.environ["CHEESE_TOKEN"])
        token_path.chmod(0o600)
    if forwarded:
        context_tree = sync_context(target_path, context_tree)
    # Deferred, not lifted with its siblings above: a test replaces `mount_state`
    # on the `release` module, so this reads it through the import each time.
    if __package__:
        # deferred-import: tests replace mount_state on the release module
        from .release import MOUNT_LIVE, mount_state, release_mount
    else:
        # deferred-import: tests replace mount_state on the release module
        from release import MOUNT_LIVE, mount_state, release_mount

    mount_log = directory / "forwarded-project.log"
    if forwarded and not local and mount_state(workspace) != MOUNT_LIVE:
        # A previous mount whose server died still occupies this directory and
        # cannot be mounted over, so without this the spawn below fails and the
        # room is stuck reporting a mount failure on every turn from then on.
        release_mount(workspace)
        with mount_log.open("a") as output:
            subprocess.Popen(
                [
                    sys.executable,
                    str(Path(__file__).with_name("forwarded_fs.py")),
                    str(target_path),
                    str(workspace),
                ],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=output,
                start_new_session=True,
            )
        deadline = time.monotonic() + 10
        while mount_state(workspace) != MOUNT_LIVE and time.monotonic() < deadline:
            time.sleep(0.05)
        if mount_state(workspace) != MOUNT_LIVE:
            detail = mount_log.read_text()[-1000:] if mount_log.exists() else ""
            raise RuntimeError("Forwarded project mount failed: " + detail)
    if forwarded or unavailable:
        link_forwarded_user_context(
            directory,
            config,
            Path(seen),
            {"entries": {}} if unavailable else context_tree,
            Path(__file__).parent,
            view=workspace,
        )
    platform_tools = platform_tool_names(cheese_source().read_text())
    plugin = write_plugin(directory / "plugin", target, platform_tools, target_path)
    settings = json.loads(json.dumps(base_settings or {}))
    # The project's own tool hooks, which the build fires and the shell prefix
    # runs on the executor (never here: they are not in `central_hooks`).
    # Recorded beside the target, so a machine that attaches later replaces
    # exactly these (`executor_transport.register_project_hooks`).
    project_hooks = (context_tree or {}).get("hooks") or {}
    for event, groups in project_hooks.items():
        settings.setdefault("hooks", {}).setdefault(event, []).extend(groups)
    (directory / "project-hooks.json").write_text(json.dumps(project_hooks))
    allow_native_tools(settings, platform_tools)
    hooks = settings.setdefault("hooks", {})
    helper = [sys.executable, str(Path(__file__).resolve())]
    guard = shlex.join([*helper, "guard", str(target_path)])
    # The file tools the plugin runs remotely. Bash is not guarded: the build
    # runs it, and the shell prefix sends it to the executor. The pinned serve
    # build has no Glob/Grep. Keep their local guard even though they are not
    # invocable remotely: another interactive build must not search the
    # session host when it offers them.
    guarded = NATIVE_TOOLS + ("Glob", "Grep")
    hooks.setdefault("PreToolUse", []).insert(
        0,
        {
            "matcher": "|".join((*guarded, "EnterWorktree", "ExitWorktree")),
            "hooks": [{"type": "command", "command": guard}],
        },
    )
    if not forwarded:
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
                                    Path(__file__)
                                    .with_name("context_service.py")
                                    .resolve()
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
    _ensure_sync_agents_hook(hooks)
    settings.update(
        skipDangerousModePermissionPrompt=True,
        enableArtifact=False,
        attribution={"sessionUrl": False},
    )
    settings_path = directory / "settings.json"
    settings_path.write_text(json.dumps(settings))
    gates = {
        "hasCompletedOnboarding": True,
        "autoUpdates": False,
        "bypassPermissionsModeAccepted": True,
        "projects": {
            seen: {
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
    # The machine's stdio servers, the type's and the remote ones share one
    # bridge; `RemoteClient.call` sends each where it is served.
    bridged = session_servers(target)
    for name in bridged:
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
        # Off, and not `auto`: model traffic may be routed by the metering proxy
        # to Anthropic-compatible gateways (MiMo, Kimi, GLM, DeepSeek via
        # LiteLLM) that do not support tool search's `tool_reference` /
        # `defer_loading`. `auto` would switch deferral on only once a room's
        # MCP definitions pass 10% of the window, and then fail there.
        "ENABLE_TOOL_SEARCH": "false",
        EXECUTION_CONFIG: str(target_path),
        "CLAUDE_CODE_TMPDIR": str(temporary),
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
        for mode in ("guard", "context", "checkpoint", "transport", "reply")
    )
    local_commands.update(
        shlex.join([*helper, "bridge", str(target_path), name]) for name in bridged
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
        # Exclude host ancestor instructions while retaining the room's user
        # context; test_session_host_files_stay_out_of_the_prompt guards this.
        "--setting-sources",
        "user",
        "--settings",
        str(settings_path),
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
        # The session enters its own namespace first, where the project is at
        # `workspace`; `cwd` is where this host holds it, for starting there.
        "command": command if local else [*helper, "enter", str(target_path), *command],
        "env": env,
        "cwd": seen if local else str(workspace),
        "workspace": seen,
        "execution": str(target_path),
    }
    (directory / "launch.json").write_text(json.dumps(launch))
    return launch


def _take_leased_machine(target):
    """A session whose machine was leased before it started starts on it.

    The platform names where the machine holds the project (`central_provider`)
    only when the machine answered this turn, so the session is relaunched onto
    it then, and here takes the machine the way a placeholder session's first
    command does: through the lease. It starts as one started on that machine
    would, with the project's instructions, hooks and MCP servers. A lease the
    platform cannot hand out now fails the start, loudly: a session at the
    machine's path without the machine is not one that machine would run.
    """
    client = RemoteClient(dict(target))
    # Taken as it is, with no wait for a machine being prepared: the platform
    # asked the machine this turn, and the turn is not held for it.
    client.acquire(deadline=time.monotonic())
    return client.config


def sync_context(target_path, supplied_tree=None):

    target = json.loads(Path(target_path).read_text())
    if target.get("kind") not in {"private", "unavailable"}:
        tree = supplied_tree or RemoteClient(target).call(
            "context_fs", {"operation": "tree"}
        )
        unsupported = tree.get("unsupported_imports", []) + tree.get(
            "unsupported_paths", []
        )
        if unsupported:
            raise RuntimeError(
                "Project context leaves the forwarded project boundary: "
                + ", ".join(unsupported)
            )
        generation_path = Path(target_path).parent / "context-generation"
        tree_path = Path(target_path).parent / "context-tree.json"
        previous = generation_path.read_text() if generation_path.exists() else None
        tree["changed"] = tree["generation"] != previous
        if tree["changed"]:
            temporary = tree_path.with_name(tree_path.name + ".next")
            temporary.write_text(json.dumps(tree))
            temporary.replace(tree_path)
            generation_path.write_text(tree["generation"])
            if "session_workspace" in target:
                # A session already running: the skills, commands, agents and
                # rules the project has now are the ones linked into its
                # config dir. (`prepare` links them itself, once mounted.)
                link_forwarded_user_context(
                    Path(target_path).parent,
                    target["central_config"],
                    Path(target["session_workspace"]),
                    tree,
                    Path(__file__).parent,
                    view=target["central_workspace"],
                )
        return tree
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
        {
            "files": {},
            "instructions": MACHINE_OUT_OF_REACH
            if target.get("kind") in {"unavailable", "deferred"}
            else PRIVATE_INSTRUCTIONS,
        }
        if target.get("kind") in {"private", "unavailable", "deferred"}
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
            elif link.resolve() != source.resolve():
                link.unlink()
                link.symlink_to(source, target_is_directory=True)
    return snapshot


def _same_file(first, second):
    try:
        a, b = os.fstat(first), os.fstat(second)
    except OSError:
        return False
    return stat.S_ISREG(a.st_mode) and (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view) :]


def run_in_place(argv, env=None):
    """Run argv as this process: exec it, where there is exec.

    Windows has none. Its os.exec* starts a new process and ends this one, and
    whoever waits on this one — the runner on its agent, Claude Code on a
    command — takes the program for ended while it runs on, outside the tree
    that is ended with the session. There it runs as a child, on the handles
    this process holds, and this process ends the way it ended."""
    if sys.platform != "win32":
        if env is None:
            os.execvp(argv[0], argv)
        os.execvpe(argv[0], argv, env)
    path = (env if env is not None else os.environ).get("PATH")
    program = shutil.which(argv[0], path=path) or argv[0]
    try:
        code = subprocess.call([program, *argv[1:]], env=env)
    except KeyboardInterrupt:
        code = 130
    raise SystemExit(code)


def _under(path, root):
    return bool(root) and (path == root or path.startswith(root + "/"))


def shell(target_path, command):
    """One command the build hands its shell prefix: run where it belongs.

    A platform command this host trusts, matched whole, runs here. Anything
    else runs on the executor, and this process stands in for it towards the
    build: it prints what the command prints as it prints it, ends when it
    ends and the way it ended, passes on a signal the build sends it, and for
    a Bash command tells the build which directory the shell ended in. The
    command itself is the executor's, not this process's: a dropped link only
    pauses the reading, and the reader picks up at the byte it had reached.
    """
    target = json.loads(Path(target_path).read_text())
    words = shlex.split(command)
    helper = str(Path(__file__).resolve())
    # Only the platform's own transport and guard run on the central host.
    if (
        len(words) >= 4
        and words[:2] == [sys.executable, helper]
        and words[2]
        in ("bridge", "guard", "context", "checkpoint", "transport", "reply")
        and words[3] == str(target_path)
    ):
        run_in_place(words)
    commands = {
        hook["command"]
        for groups in target.get("central_hooks", {}).values()
        for group in groups
        for hook in group.get("hooks", [])
        if hook.get("type") == "command"
    }
    if command in commands:
        run_in_place(["sh", "-c", command])
    # The build hands this process's stdout and stderr to the command: they
    # are the command's output, and nothing of the platform's may be in them.
    # What the transport logs on its way through a retry (a 409 while the
    # device reconnects, say) goes to this session's own log instead. Only a
    # command that could not run at all says so there, in the platform's words.
    logging.basicConfig(
        filename=str(Path(target_path).parent / "shell-prefix.log"),
        format="%(asctime)s %(process)d %(levelname)s %(message)s",
        level=logging.INFO,
    )
    return run_on_the_machine(target, command)


def skill_places(target):
    """Where the executor holds each skill the session names by an entry that
    is not the project's root skill of that name: a subdirectory's, and one
    written without its `paths` (`release.SKILL_PLACES`)."""
    path = Path(target.get("target_file", "")).parent / "skill-places.json"
    try:
        places = json.loads(path.read_text())["places"]
    except (OSError, ValueError, KeyError):
        return {}
    return {name: place["place"] for name, place in places.items()}


def skill_paths(target, text):
    """`text` with each skill file named in the session's config dir named
    where the executor holds it: a project skill in the project's
    `.claude/skills`, one the platform shipped in the executor's config dir.
    `proxy.js` `skillPath` is the same mapping, for the file tools."""
    skills = target["central_config"] + "/skills/"
    project = session_path(target["workspace"]) + "/.claude/skills/"
    shipped = set(target.get("shipped_skills") or [])
    executor = target.get("executor_config")
    places = skill_places(target)

    def place(match):
        name = match.group(1)
        if name in places:
            return places[name]
        if name in shipped:
            return f"{executor}/skills/{name}" if executor else match.group(0)
        return project + name

    return re.sub(re.escape(skills) + r"([^/\s'\"`]+)", place, text)


def run_on_the_machine(target, command):
    # The session sees the project at the executor's own path, so a command and
    # its directory need no respelling. Its skills are the one thing the build
    # reads from this host's config directory (`link_forwarded_user_context`),
    # where the executor has none: a command naming a skill's file names it in
    # the project.
    def outward(text):
        return skill_paths(target, text)

    cwd = os.getcwd()
    head, tail = _SNAPSHOT.match(command), _CWD_FILE.search(command)
    bash = tail is not None
    if bash:
        # A Bash tool command: the executor wraps its body with its own
        # snapshot and cwd file. Its stdin is `/dev/null` by construction.
        body = command[head.end() if head else 0 : tail.start()]
        stdin = None
    else:
        # A hook: its input arrives on stdin, spelled for this host.
        body = command
        data = b""
        with os.fdopen(os.dup(0), "rb") as source:
            data = source.read(SHELL_INPUT_BYTES + 1)
        if len(data) > SHELL_INPUT_BYTES:
            raise SystemExit("Hook input is over the executor's limit")
        stdin = base64.b64encode(
            outward(data.decode("utf-8", "surrogateescape")).encode(
                "utf-8", "surrogateescape"
            )
        ).decode()
    env = {name: os.environ[name] for name in FORWARDED_ENV if name in os.environ}
    if "CLAUDE_PROJECT_DIR" in os.environ:
        env["CLAUDE_PROJECT_DIR"] = os.environ["CLAUDE_PROJECT_DIR"]
    merge = _same_file(1, 2)
    command_id = "shell-" + uuid.uuid4().hex
    client = RemoteClient(target)

    def call(operation, **params):
        return client.call(
            "control",
            {
                "subtype": "shell",
                "operation": operation,
                "command_id": command_id,
                **params,
            },
            preparing=sys.stderr if bash else None,  # a hook's stderr is its answer
        )

    # A stop has to reach the command even when this process does not live to
    # send it: the build follows its TERM with a KILL about a second later, and
    # a command whose start was still on its way would otherwise run on with
    # nobody left to stop it. So a watcher that outlives this process delivers
    # it (`shell_stop.deliver_stop`), told through a pipe: `stop <n>` when the
    # build asks, `done` when the command has ended, and nothing — the pipe
    # closing — when this process was killed first.
    watcher = start_watcher(target, command_id)

    def on_signal(number, _frame):
        with contextlib.suppress(OSError):
            os.write(watcher, f"stop {number}\n".encode())

    for number in CAUGHT:
        signal.signal(number, on_signal)

    # Started once, whatever it takes: the id makes a retried start the same start.
    deadline = time.monotonic() + SHELL_START_RETRY_S
    while True:
        try:
            call(
                "start",
                kind="bash" if bash else "sh",
                body=outward(body),
                cwd=cwd,
                env=env,
                merge=merge,
                stdin=stdin,
            )
            break
        except MachineOutOfReach:
            if target.get("kind") == "unavailable" or time.monotonic() > deadline:
                sys.stderr.write(MACHINE_OUT_OF_REACH + "\n")
                return 1
        except (OSError, TimeoutError):
            if time.monotonic() > deadline:
                sys.stderr.write(MACHINE_OUT_OF_REACH + "\n")
                return 1
        except RuntimeError as exc:
            if "upgrading" not in str(exc) or time.monotonic() > deadline:
                sys.stderr.write(f"{exc}\n")
                return 1
        time.sleep(1)
    offsets = {"out": 0, "err": 0}
    written = 0
    failing_since, delay = None, SHELL_READ_RETRY_S[0]
    answer = {}
    while True:
        try:
            answer = call("read", out=offsets["out"], err=offsets["err"], wait=20)
            failing_since, delay = None, SHELL_READ_RETRY_S[0]
        except Exception as exc:  # noqa: BLE001 — the command outlives the link
            link = isinstance(exc, MachineOutOfReach | OSError | TimeoutError)
            failing_since = failing_since or time.monotonic()
            if not link and time.monotonic() - failing_since > SHELL_ERROR_RETRY_S:
                sys.stderr.write(f"{exc}\n")
                return 1
            client = RemoteClient(current_target(target))
            time.sleep(delay)
            delay = min(delay * 2, SHELL_READ_RETRY_S[1])
            continue
        if answer.get("lost"):
            sys.stderr.write(
                "The command's executor restarted; its outcome is unknown\n"
            )
            return 1
        for stream, fd in (("out", 1), ("err", 2)):
            data = base64.b64decode(answer.get(stream) or "")
            offsets[stream] += len(data)
            written += len(data)
            if written > SHELL_OUTPUT_BYTES:
                with contextlib.suppress(Exception):
                    call("signal", signal=KILL)
                sys.stderr.write(
                    f"\nOutput passed {SHELL_OUTPUT_BYTES // (1024 * 1024)} MiB; "
                    "the command was stopped.\n"
                )
                return 1
            _write_all(fd, data)
        if "exit" in answer:
            break
    for number in CAUGHT:
        signal.signal(number, signal.SIG_IGN)
    with contextlib.suppress(OSError):
        os.write(watcher, b"done\n")
    if bash and "cwd" in answer:
        _report_directory(target, client, answer["cwd"], tail.group(1))
    with contextlib.suppress(Exception):
        call("forget")
    code = answer["exit"]
    if code < 0 and sys.platform == "win32":
        # No signal to end with here: the shell's way of saying it, 128 + n.
        return 128 - code
    if code < 0:
        # Killed by a signal there: end the same way here, as the shell the
        # build started would have.
        # SIGKILL's disposition cannot be set, and needs no resetting.
        with contextlib.suppress(OSError, ValueError):
            signal.signal(-code, signal.SIG_DFL)
        os.kill(os.getpid(), -code)
    return code


def _report_directory(target, client, final, spelled):
    """Tell the build which directory the command's shell ended in.

    The build reads it from the cwd file it named and keeps it only if the
    directory exists here. A directory in the workspace does, at the same path,
    in the session's view of the project. One outside the workspace is
    reported as `/`, which the build answers exactly as a native session does:
    it resets to the workspace root and says so.
    """
    path = Path(shlex.split(spelled)[0])
    temporary = os.environ.get("CLAUDE_CODE_TMPDIR")
    # Only the cwd file the build itself chose: a hook shaped like a Bash
    # command must not steer this write anywhere else on this host.
    if (
        not temporary
        or path.parent != Path(temporary)
        or not re.fullmatch(r"claude-[0-9a-f]+-cwd", path.name)
    ):
        return
    # A session started before its machine was rented sees the project at a
    # placeholder until it is relaunched on the machine (`_take_leased_machine`);
    # the lease (`RemoteClient.call`) points its commands at the machine's
    # workspace meanwhile.
    machine = session_path(client.config["workspace"])
    seen = session_path(client.config.get("virtual_workspace", machine))
    final = final[:4096]
    if _under(final, machine):
        spelled_here = seen + final[len(machine) :]
        if target.get("kind") == "private":
            # The private scratch view is a plain directory here.
            Path(spelled_here).mkdir(parents=True, exist_ok=True)
    else:
        spelled_here = "/"
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        output.write(spelled_here + "\n")


# The namespace the session runs in (`enter`): mount(2), umount2(2) and
# pivot_root(2) flags and numbers, which the standard library does not name.
_MS_NOSUID, _MS_NODEV = 2, 4
_MS_BIND, _MS_REC, _MS_PRIVATE = 4096, 16384, 1 << 18
_CLONE_NEWNS, _CLONE_NEWUSER = 0x00020000, 0x10000000
_MNT_DETACH = 2
_SYS_PIVOT_ROOT = {"x86_64": 155, "aarch64": 41}


def enter(target_path, argv):
    """Run the session with the project at the executor's own path.

    Claude Code prints its working directory — in what it tells the model
    about the session, in a refusal to run a command, when it resets the
    shell's directory — and what it prints has to be what it prints running on
    the executor. So the session gets a user and mount namespace of its own,
    in which this host's view of the project is mounted at exactly the path
    the executor holds it at, and runs there. Nothing else in it moves: every
    other path is this host's own, bound into place. The directories made to
    lead down to the project are the session's own: what is already in them
    is this host's, and what the session creates directly in one (a socket in
    `/tmp` when the project is under it) stays in the session, as in a private
    `/tmp`.

    A path that cannot be placed so is refused, loudly: one that is not an
    absolute path, one under the kernel's own filesystems, and one that would
    cover something the session itself needs.
    """
    target = json.loads(Path(target_path).read_text())
    seen = target["session_workspace"]
    view = target["central_workspace"]
    program = shutil.which(argv[0])
    if not program:
        raise SystemExit(f"{argv[0]}: not found")
    program = os.path.abspath(program)
    needed = [
        str(Path(target_path).parent),
        target["central_config"],
        target["central_tmp"],
        os.environ.get("HOME", ""),
        str(Path(__file__).resolve().parent),
        sys.executable,
        program,
        view,
    ]
    problem = _unplaceable(seen, needed)
    if problem:
        raise SystemExit(
            f"This session cannot see its project at {seen!r}, the executor's "
            f"path: {problem}"
        )
    _place(seen, view, Path(target_path).parent / "namespace-root")
    os.chdir(seen)
    os.environ["PWD"] = seen
    os.execv(program, argv)


def _unplaceable(seen, needed):
    """Why `seen` cannot hold the project in the session's namespace, or None."""
    if sys.platform != "linux":
        return "the session host must be Linux, for its user and mount namespaces"
    if not seen.startswith("/") or seen.startswith("//"):
        return "it is not an absolute path"
    if os.path.normpath(seen) != seen or seen == "/":
        return "it is not a normalized path below /"
    if seen.split("/")[1] in ("proc", "sys", "dev"):
        return "it is under a kernel filesystem"
    for path in needed:
        for spelled in {path, os.path.realpath(path)} if path else ():
            if spelled == seen or spelled.startswith(seen + "/"):
                return f"it would cover {spelled}, which the session needs"
    return None


def _place(seen, view, root):
    """Enter a user and mount namespace whose root is this host's, with `view`
    mounted at `seen`.

    The new root is a tmpfs holding this host's top-level entries, each bound
    into place. Every directory on the way down to `seen` is rebuilt the same
    way, with its other entries bound in and the next one made anew, since an
    unprivileged namespace can mount over a directory but not create one in a
    directory it does not own. The user namespace maps this user to itself,
    so files keep their owner, and the capabilities it grants end at exec.
    """

    libc = ctypes.CDLL(None, use_errno=True)

    def check(result, what):
        if result != 0:
            number = ctypes.get_errno()
            # OSError(errno, ...) is the subclass the errno names, so a source
            # that vanished is a FileNotFoundError.
            raise OSError(number, f"{what}: {os.strerror(number)}")

    def mount(source, place, kind, flags, data=None):
        check(
            libc.mount(
                source.encode() if source else None,
                place.encode(),
                kind.encode() if kind else None,
                flags,
                data.encode() if data else None,
            ),
            f"mount {place}",
        )

    uid, gid = os.getuid(), os.getgid()
    root = str(root)
    os.makedirs(root, exist_ok=True)
    check(libc.unshare(_CLONE_NEWUSER | _CLONE_NEWNS), "unshare")
    for name, value in (
        ("setgroups", "deny"),
        ("uid_map", f"{uid} {uid} 1"),
        ("gid_map", f"{gid} {gid} 1"),
    ):
        with open(f"/proc/self/{name}", "w") as stream:
            stream.write(value)
    mount(None, "/", None, _MS_REC | _MS_PRIVATE)
    # Bounded: it is memory, on the host every session runs on.
    mount("tmpfs", root, "tmpfs", _MS_NOSUID | _MS_NODEV, "mode=755,size=64m")

    def rebuild(real, copy, skip):
        # A bind that is not recursive is refused in a user namespace when the
        # directory has mounts under it, so every bind here is recursive.
        for entry in os.scandir(real):
            if entry.name == skip:
                continue
            place = os.path.join(copy, entry.name)
            try:
                if entry.is_symlink():
                    os.symlink(os.readlink(entry.path), place)
                    continue
                if entry.is_dir(follow_symlinks=False):
                    os.mkdir(place)
                else:
                    open(place, "w").close()
                mount(entry.path, place, None, _MS_BIND | _MS_REC)
            except FileNotFoundError:
                # Gone since it was listed (a busy /tmp): not there to show.
                if os.path.isdir(place) and not os.path.islink(place):
                    os.rmdir(place)
                elif os.path.lexists(place):
                    os.unlink(place)

    names = seen.strip("/").split("/")
    real, copy = "/", root
    for name in names:
        if real is not None:
            rebuild(real, copy, name)
        copy = os.path.join(copy, name)
        os.mkdir(copy)
        below = os.path.join(real, name) if real is not None else None
        # A directory reached through a symlink (`/lib` on a merged-/usr
        # system) is rebuilt from where it leads, so nothing in it goes missing.
        real = (
            os.path.realpath(below)
            if below is not None and os.path.isdir(below)
            else None
        )
    mount(view, copy, None, _MS_BIND | _MS_REC)
    old = os.path.join(root, ".host-root")
    os.mkdir(old)
    os.chdir(root)
    number = _SYS_PIVOT_ROOT.get(os.uname().machine)
    if number is None:
        raise OSError(f"pivot_root: no syscall number for {os.uname().machine}")
    check(libc.syscall(number, b".", b".host-root"), "pivot_root")
    os.chdir("/")
    check(libc.umount2(b"/.host-root", _MNT_DETACH), "umount /.host-root")
    os.rmdir("/.host-root")


MAX_SEND_USER_FILE_BYTES = 10 * 1024 * 1024

# What the tool result promises the caller about the file: `isImage` for the
# suffixes that are pictures, `media_type` for what the bytes are. The room
# types the artifact off the path on `POST /topics/{id}/shown`
# (`room_files.ARTIFACT_MIME`); this tool never declares `as`, so that table is
# the only one that names a kind. These two fields describe the file to the
# caller — they are not a second copy of the room's kind table.
_SEND_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_SEND_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation"
    ),
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def send_user_file_paths(path, config):
    """`(executor path, room path)` for one file a SendUserFile named.

    The model spells paths the way the session was told to (the executor's
    workspace); the room wants a workspace-relative pointer because
    `POST /topics/{id}/shown` refuses absolute ones. A file that lives outside
    the workspace keeps its name under `uploads/`, the address this room already
    gives a paste with no name of its own.
    """
    path = (path or "").replace("\\", "/")
    work = session_path(config.get("workspace") or "").rstrip("/")
    machine = path
    if work and not machine.startswith("/"):
        # The executor's shell keeps its own cwd across commands; a relative
        # path is only unambiguous once it is anchored at the workspace.
        machine = work + "/" + machine
    name = machine.rsplit("/", 1)[-1] or "file"
    rel = machine
    if work and (machine == work or machine.startswith(work + "/")):
        rel = machine[len(work) :].lstrip("/")
    parts = rel.split("/") if rel else []
    if not rel or rel.startswith("/") or ".." in parts or ".git" in parts:
        rel = f"uploads/{uuid.uuid4().hex}/{name}"
    return machine, rel


def send_user_file_entry(path, name, size, upload_error=None):
    suffix = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    entry = {
        "path": path,
        "size": size,
        "isImage": suffix in _SEND_IMAGE_SUFFIXES,
        "media_type": _SEND_MEDIA_TYPE.get(suffix, "application/octet-stream"),
        "pathValidated": upload_error is None,
    }
    if upload_error is not None:
        entry["upload_error"] = upload_error
    return entry


def send_user_file_body(raw):
    """The `POST /topics/{id}/shown` body for one file's bytes.

    `content_b64` for every kind, not just the binary ones: the room route
    already takes office and PDF bytes that way, and a caller that decoded to
    text first cannot carry them. The kind is not declared here — the route
    reads it off `path`, so this and the room's table cannot drift.
    """
    return {"content_b64": base64.b64encode(raw).decode("ascii")}


def deliver_send_user_file(client, config, payload, args, invoke):
    """Hand each file to this room, and the caption beside them.

    Delivery is `POST /topics/{id}/shown` — the route `cheese show` already
    publishes through (SKILL.md), which lands an artifact the room renders and
    offers for download. `POST /topics/{id}/attachments` is the input bar's
    staging area: a file parked there is waiting on a message that never comes.

    Each result entry's `path` is the file's resolved filesystem path (the
    executor path `send_user_file_paths` computes), what `SendUserFile` promises
    the caller. The room-relative pointer is the `path` on the POST body — a
    different field, for a different reader.
    """
    topic = os.environ.get("CHEESE_TOPIC", "")
    if not topic:
        raise RuntimeError("Delivering a file requires room credentials")
    attachments = []
    delivered = False
    for index, item in enumerate(args.get("files") or []):
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            raise RuntimeError(
                "SendUserFile cannot deliver a pre-resolved "
                "{file_uuid, file_name, size, is_image} object; "
                "pass a file path instead"
            )
        path = str(item.get("path") or "")
        name = str(item.get("name") or "") or (
            path.replace("\\", "/").rsplit("/", 1)[-1] or "file"
        )
        machine, rel = send_user_file_paths(path, config)
        preexisting = item.get("upload_error")
        if preexisting:
            attachments.append(
                send_user_file_entry(machine, name, 0, upload_error=str(preexisting))
            )
            continue
        try:
            if item.get("data_b64"):
                raw = base64.b64decode(item["data_b64"], validate=True)
            else:
                # The plugin's stat never saw this file; refuse an oversize one
                # before `base64` walks it across the executor connection.
                size = stat_file_on_the_machine(
                    invoke, machine, f"{payload['id']}-file-{index}-stat"
                )
                if size > MAX_SEND_USER_FILE_BYTES:
                    attachments.append(
                        send_user_file_entry(
                            machine,
                            name,
                            size,
                            upload_error=(
                                f"文件太大（上限 "
                                f"{MAX_SEND_USER_FILE_BYTES // (1024 * 1024)}MB）"
                            ),
                        )
                    )
                    continue
                raw = read_file_on_the_machine(
                    invoke, machine, f"{payload['id']}-file-{index}"
                )
        except Exception as exc:
            attachments.append(
                send_user_file_entry(machine, name, 0, upload_error=str(exc))
            )
            continue
        if not raw:
            attachments.append(
                send_user_file_entry(machine, name, 0, upload_error="空文件")
            )
            continue
        if len(raw) > MAX_SEND_USER_FILE_BYTES:
            attachments.append(
                send_user_file_entry(
                    machine,
                    name,
                    len(raw),
                    upload_error=(
                        f"文件太大（上限 "
                        f"{MAX_SEND_USER_FILE_BYTES // (1024 * 1024)}MB）"
                    ),
                )
            )
            continue
        try:
            client.platform_request(
                {
                    "method": "POST",
                    "path": f"/topics/{topic}/shown",
                    "body": {"path": rel, **send_user_file_body(raw)},
                }
            )
        except Exception as exc:
            attachments.append(
                send_user_file_entry(machine, name, len(raw), upload_error=str(exc))
            )
            continue
        attachments.append(send_user_file_entry(machine, name, len(raw)))
        delivered = True
    caption = args.get("caption")
    if delivered and isinstance(caption, str) and caption.strip():
        client.publish_message(payload, {"content": caption.strip()})
    result: dict[str, Any] = {"attachments": attachments}
    if isinstance(caption, str):
        result["caption"] = caption
    if isinstance(args.get("display"), str):
        result["display"] = args["display"]
    return {"value": result}


def reached_skills(target_path, target, args):
    """A file tool reached a file on the executor: the session offers what
    Claude Code offers once one has (`release.touch_skills`). The tool's own
    result stands whatever happens here."""
    path = args.get("file_path") or args.get("notebook_path")
    if not isinstance(path, str) or "session_workspace" not in target:
        return
    try:
        touch_skills(
            Path(target_path).parent,
            target["central_config"],
            target["session_workspace"],
            Path(__file__).parent,
            path,
            view=target.get("central_workspace"),
        )
    except Exception as error:  # noqa: BLE001 — the call it followed succeeded
        print(f"skills not updated after {path}: {error!r}", file=sys.stderr)


def transport(config, target_path):

    client = RemoteClient(config)
    cheese = SourceFileLoader(
        "cheese_request_plans", str(cheese_source())
    ).load_module()
    output_lock = threading.Lock()
    active = {}
    active_lock = threading.RLock()
    cancelled = set()
    # 地点没了，项目工具就是不可用（结论 23）——**如实标出来，不让它们各自超时**。
    # 一次够不着的调用要走完执行器连接的读超时（660s），而 agent 手上一整轮的文件与
    # 命令调用会一个接一个各撞一次。第一次撞上之后，余下的当场答同一句话：机器够不
    # 着这件事第一次就问清楚了，后面每一次都是在重问。
    #
    # 平台工具从会话直接打后端，不经过这台机器；只有要机器的那一样
    # （`cheese_accept_request` 推提交）走这个出口，于是也吃这个闸：当场说够不着，
    # 而不是等超时。
    #
    # 再试一次的那个口子留着，因为「够不着」是这一刻的事实，不是这一场会话的判决：
    # 一次 502 之后机器回来了，而闸没有第二个开关。
    unreachable_since: list[float | None] = [None]
    RECHECK_AFTER_S = 30

    def cancel(request_id):
        with active_lock:
            if request_id in active:
                cancelled.add(request_id)

    def stop(signum, _frame):
        with active_lock:
            requests = list(active)
        for request_id in requests:
            cancel(request_id)
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    def invoke_on_the_machine(payload, args, abandoned=None, preparing=None):
        """项目工具的唯一出口 —— 机器够不着时它当场答，不去撞那条超时。"""
        gone_for = (
            None
            if unreachable_since[0] is None
            else time.monotonic() - unreachable_since[0]
        )
        if gone_for is not None and gone_for < RECHECK_AFTER_S:
            return {"error": MACHINE_OUT_OF_REACH}
        try:
            receipt = client.call(
                "invoke",
                {"id": payload["id"], "tool": payload["tool"], "args": args},
                abandoned=abandoned,
                preparing=preparing,
            )
        except MachineOutOfReach:
            unreachable_since[0] = time.monotonic()
            raise
        unreachable_since[0] = None
        return receipt

    # 读到的是哪一版实况文档，写回时要出示（`sandbox/cheese` 的 `_doc_get`）。
    # 活在这条会话的 MCP 进程里：进程重起就当没读过，那是安全的方向。
    doc_versions: dict[str, int] = {}

    def platform_tool(tool, args, call_id, invoke):
        host = PlatformHost(client, invoke, call_id, doc_versions)
        try:
            text = cheese.run_platform_tool(tool, args, host)
        except MachineOutOfReach:
            return {"error": MACHINE_OUT_OF_REACH}
        except Exception as exc:  # noqa: BLE001 — the agent reads the reason
            return {"error": str(exc)}
        return {"value": {"stdout": text, "stderr": ""}}

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
                            "name": "project_tools",
                            "description": (
                                "Discover and call configured project MCP tools "
                                "on the work machine. Omit server to discover; "
                                "provide server to list tools; add name and "
                                "arguments to call one. "
                                "This acquires work equipment only when invoked."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "session_id": {"type": "string"},
                                    "server": {"type": "string"},
                                    "name": {"type": "string"},
                                    "arguments": {"type": "object"},
                                },
                                "required": ["id", "session_id"],
                            },
                        },
                        {
                            "name": "permission",
                            "description": "Internal. Call Bash instead.",
                            "inputSchema": {"type": "object"},
                        },
                        {
                            "name": "send_user_file",
                            "description": (
                                "Internal delivery transport for the built-in "
                                "SendUserFile tool: hands files to this room on "
                                "the session's own credentials. Call SendUserFile "
                                "instead of this."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string"},
                                    "session_id": {"type": "string"},
                                    "files": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "path": {"type": "string"},
                                                "name": {"type": "string"},
                                                "data_b64": {"type": "string"},
                                                "upload_error": {"type": "string"},
                                            },
                                            "required": ["path"],
                                        },
                                    },
                                    "caption": {"type": "string"},
                                    "status": {"type": "string"},
                                    "display": {"type": "string"},
                                },
                                "required": ["id", "session_id", "files"],
                            },
                        },
                    ]
                    + cheese.PLATFORM_TOOLS.schemas()
                }
            elif method == "tools/call":
                tool = request["params"]["name"]
                if tool not in (
                    "invoke",
                    "permission",
                    "send_user_file",
                    "project_tools",
                ) and (tool not in cheese.PLATFORM_TOOLS):
                    raise ValueError("Unknown transport tool")
                payload = request["params"]["arguments"]
                # The arguments each of these takes, from the call's.
                picked = {
                    "chat_send": ("content", "reply_to", "request_id"),
                    "project_tools": ("server", "name", "arguments"),
                }
                if tool in picked:
                    payload = {
                        "id": payload["id"],
                        "session_id": payload["session_id"],
                        "tool": "mcp__native__" + tool,
                        "args": {k: payload[k] for k in picked[tool] if k in payload},
                    }
                elif tool == "send_user_file" or tool in cheese.PLATFORM_TOOLS:
                    # Every other row of the table. Membership, not a `cheese_`
                    # prefix, decides: `todo_write` carries none.
                    payload = {
                        "id": payload["id"],
                        "session_id": payload["session_id"],
                        "tool": "SendUserFile"
                        if tool == "send_user_file"
                        else "mcp__native__" + tool,
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
                args = payload["args"]
                # A call that waits for its machine is told so (`acquire`).
                notice = io.StringIO()
                invoke = functools.partial(invoke_on_the_machine, preparing=notice)

                def abandoned():
                    # Checked again once the machine is ready: a call cancelled
                    # while it was being prepared never starts.
                    with active_lock:
                        return request["id"] in cancelled

                if tool == "project_tools":
                    receipt = {
                        "value": client.call(
                            "project_tools",
                            {**args, "id": payload["id"]},
                            abandoned=abandoned,
                            preparing=notice,
                        )
                    }
                elif tool == "send_user_file":
                    receipt = deliver_send_user_file(
                        client, config, payload, args, invoke
                    )
                elif tool != "chat_send" and tool in cheese.PLATFORM_TOOLS:
                    receipt = platform_tool(tool, args, payload["id"], invoke)
                else:
                    receipt = (
                        client.publish_message(payload, args)
                        if tool == "chat_send"
                        else client.permission(payload, args, notice)
                        if tool == "permission"
                        else invoke(payload, args, abandoned)
                    )
                if "error" in receipt:
                    outcome = {"deny": receipt["error"]}
                else:
                    outcome = {"result": receipt["value"]}
                    if tool == "invoke":
                        reached_skills(target_path, config, args)
                if notice.getvalue():
                    outcome["context"] = [notice.getvalue().strip()]
                image = outcome.get("result", {})
                if isinstance(image, dict) and image.get("type") == "image":
                    # Base64 in text hits Claude Code's MCP text-output limit.
                    # Keep native Read metadata in text and pixels in an image block.
                    image_file = image["file"]
                    metadata = {k: v for k, v in image_file.items() if k != "base64"}
                    outcome = {"result": {**image, "file": metadata}}
                    value = {
                        "content": [
                            {"type": "text", "text": json.dumps(outcome)},
                            {
                                "type": "image",
                                "data": image_file["base64"],
                                "mimeType": image_file["type"],
                            },
                        ]
                    }
                else:
                    encoded = json.dumps(outcome)
                    if len(encoded) > 32_000:
                        # MCP replaces large text with prose; the plugin needs
                        # the original receipt, including API JSON and Edit state.
                        receipts = Path(target_path).parent / "tool-results"
                        receipts.mkdir(exist_ok=True, mode=0o700)
                        receipt_path = receipts / f"{uuid.uuid4().hex}.json"
                        receipt_path.write_text(encoded)
                        receipt_path.chmod(0o600)
                        encoded = json.dumps({"receipt_path": str(receipt_path)})
                    value = {"content": [{"type": "text", "text": encoded}]}
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


def own_output(config, call):
    """A Read of what the build wrote about a command on this host: a
    background task's output file, or a large result it persisted. That read
    is local in a native session too; any other native call is refused."""
    if call.get("tool_name") != "Read":
        return False
    path = os.path.realpath(str((call.get("tool_input") or {}).get("file_path", "")))
    temporary = os.path.realpath(config.get("central_tmp", "")) + os.sep
    projects = os.path.realpath(config["central_config"]) + os.sep + "projects" + os.sep
    return (
        bool(config.get("central_tmp"))
        and path.startswith(temporary)
        or (
            path.startswith(projects)
            and os.sep + "tool-results" + os.sep in path[len(projects) :]
        )
    )


def probed(config, call):
    """The session's own Read of a project file, which `proxy.js` lets through
    to have the session offer the skills whose `paths` name it; the executor
    answers the call. Only the path the plugin named for it is admitted."""
    if call.get("tool_name") != "Read":
        return False
    try:
        named = json.loads(
            (Path(config["target_file"]).parent / "read-probe.json").read_text()
        ).get("path")
    except (OSError, ValueError, KeyError):
        return False
    return bool(named) and (call.get("tool_input") or {}).get("file_path") == named


def own_memory(config, call):
    """A file tool on the session's memory tree, which lives on this host
    (`prepare`'s `central_memory`). The plugin has already spelled the path
    out; anything that resolves outside the tree is not admitted."""
    if call.get("tool_name") not in NATIVE_TOOLS or not config.get("central_memory"):
        return False
    tool_input = call.get("tool_input") or {}
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    memory = os.path.realpath(config["central_memory"]) + os.sep
    return os.path.realpath(str(path)).startswith(memory)


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
            "catch-up",
            "control",
            "shell",
            "bootstrap",
            "enter",
            "checkpoint",
            "release",
            "transport",
            "reply",
            "watch",
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
            # deferred-import: private imports fcntl, which Windows has not
            from .private import release
        else:
            # deferred-import: private imports fcntl, which Windows has not
            from private import release

        release(config)
    elif args.mode == "bridge":
        # Remote servers and a type's own, whose definition rides each call.
        remote = set(session_servers(config)) - set(config.get("mcp_servers", []))
        if config.get("kind") == "device" or args.args[0] in remote:
            bridge(None, args.args[0], call=RemoteClient(config).call)
        else:
            command = RemoteClient(config).command("bridge", args.args[0])
            run_in_place(command)
    elif args.mode == "checkpoint":
        payload = json.load(sys.stdin)
        transcript = Path(payload["transcript_path"])
        identifier = hashlib.sha256(
            (
                json.dumps(payload, sort_keys=True) + str(transcript.stat().st_size)
            ).encode()
        ).hexdigest()
        result = RemoteClient(config).checkpoint("checkpoint-" + identifier)
        if "error" in result:
            raise RuntimeError(result["error"])
    elif args.mode == "reply":
        decision = reply_hook(json.load(sys.stdin))
        if decision is not None:
            print(json.dumps(decision))
    elif args.mode == "guard":
        call = json.load(sys.stdin)
        if own_output(config, call) or own_memory(config, call) or probed(config, call):
            return
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
    elif args.mode == "watch":
        # Windows' watcher for one command (`shell_stop.start_watcher`): what
        # to do comes on stdin, and stdin closing is the prefix gone.
        deliver_stop(config, args.args[0], sys.stdin.fileno())
    elif args.mode == "enter":
        enter(args.config, args.args)
    elif args.mode == "bootstrap":
        # The launcher names the seat's base settings separately from the
        # room config. An older seat may still be using that room's settings.
        config_dir = Path(os.environ["CLAUDE_CONFIG_DIR"])
        directory = Path(os.environ[EXECUTION_CONFIG]).parent
        launch = prepare(
            directory,
            config,
            claude=args.args[0],
            extra_args=args.args[1:],
            base_settings=json.loads((directory / "base-settings.json").read_text()),
            home_override=os.environ["HOME"],
            config_override=config_dir,
            workspace_override=os.environ["CHEESE_WORK"],
        )
        os.chdir(launch["cwd"])
        run_in_place(launch["command"], dict(os.environ, **launch["env"]))
    elif args.mode == "context":
        sync_context(args.config)
    elif args.mode == "catch-up":
        # The runner, before a turn: whether the project's context changed,
        # or a file tool reached skills the session did not offer before
        # (`release.touch_skills`).
        touched = Path(args.config).parent / "skills-changed"
        reached = touched.exists()
        touched.unlink(missing_ok=True)
        tree = sync_context(args.config)
        print(json.dumps({"changed": bool(tree.get("changed")) or reached}))
    elif args.mode == "control":
        print(json.dumps(RemoteClient(config).control(json.load(sys.stdin))))
    elif args.mode == "prepare":
        print(json.dumps(prepare(args.args[0], config, extra_args=args.args[1:])))
    else:
        env = dict(os.environ, **config["env"])
        os.chdir(config["cwd"])
        run_in_place(config["command"], env)


if __name__ == "__main__":
    main()
