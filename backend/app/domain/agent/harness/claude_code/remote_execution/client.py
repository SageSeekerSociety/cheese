"""Central session configuration and transport to an assigned executor.

The three parts it used to hold and imports from beside itself: the shell
prefix the build's commands run through (`shell_prefix.py`), the namespace a
session is entered into (`namespace.py`), and the files a turn hands a person
(`send_user_file.py`).
"""

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
import functools
import hashlib
import io
import json
import os
import shlex
import signal
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from importlib.machinery import SourceFileLoader
from pathlib import Path
from typing import Any
from urllib.parse import quote

if __package__:
    from app.domain.agent.executor_transport import (
        DEFERRED_WORKSPACE,
        MACHINE_OUT_OF_REACH,
        SESSION_SOCKET,
        WORKING_DIRECTORY,
        MachineOutOfReach,
        PlatformHost,
        RemoteClient,
        dial_runner,
        failure_text,
        session_path,
        session_servers,
        tool_answer,
    )
    from app.domain.agent.harness.claude_code.remote_execution.context_service import (
        serve,
    )
    from app.domain.agent.harness.claude_code.remote_execution.namespace import (
        enter,
    )
    from app.domain.agent.harness.claude_code.remote_execution.release import (
        EXECUTION_CONFIG,
        allow_native_tools,
        carry_conversation,
        copy_context,
        forget_touched_skills,
        hook_module,
        link_forwarded_user_context,
        platform_tool_names,
        take_view,
        touch_skills,
        write_claude_state,
    )
    from app.domain.agent.harness.claude_code.remote_execution.send_user_file import (
        chat_send_attachments,
        deliver_send_user_file,
    )
    from app.domain.agent.harness.claude_code.remote_execution.shell_prefix import (
        run_in_place,
        shell,
    )
    from app.domain.agent.harness.claude_code.remote_execution.shell_stop import (
        deliver_stop,
    )
    from app.domain.agent.harness.driven.mcp_gateway import (
        PORT_ENV,
        SECRET_ENV,
        SECRET_HEADER,
        STARTUP_S,
        Gateway,
    )
else:
    # Source scripts find the shared module in agent/; deployed bundles ship
    # the same module beside this script, which remains first on sys.path.
    sys.path.append(str(Path(__file__).resolve().parents[3]))
    sys.path.append(str(Path(__file__).resolve().parents[2] / "driven"))
    from executor_transport import (
        DEFERRED_WORKSPACE,
        MACHINE_OUT_OF_REACH,
        SESSION_SOCKET,
        WORKING_DIRECTORY,
        MachineOutOfReach,
        PlatformHost,
        RemoteClient,
        dial_runner,
        failure_text,
        session_path,
        session_servers,
        tool_answer,
    )

    # Its siblings sit beside it, which a script run has first on sys.path and
    # a file loaded by path has not.
    if str(Path(__file__).resolve().parent) not in sys.path:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from context_service import serve
    from mcp_gateway import (  # pyright: ignore[reportMissingImports] — ships beside
        PORT_ENV,
        SECRET_ENV,
        SECRET_HEADER,
        STARTUP_S,
        Gateway,
    )
    from namespace import enter
    from release import (
        EXECUTION_CONFIG,
        allow_native_tools,
        carry_conversation,
        copy_context,
        forget_touched_skills,
        hook_module,
        link_forwarded_user_context,
        platform_tool_names,
        take_view,
        touch_skills,
        write_claude_state,
    )
    from send_user_file import chat_send_attachments, deliver_send_user_file
    from shell_prefix import run_in_place, shell
    from shell_stop import deliver_stop

PINNED_VERSION = "2.1.299"
# How long a request to the runner may take before the session's own helper
# gives up on it (`compact`). The runner answers at once — the compaction it
# then writes runs for minutes and is not waited for — so this is only the
# socket's own patience.
RUNNER_REQUEST_S = 30.0
# The preload the device launch hands the build in BUN_OPTIONS
# (`device_launch.py`); a private chat's WebFetch depends on it.
PAGE_TRANSPORT = "webfetch_transport.cjs"
# The file tools the plugin runs on the executor. Bash is not one: the build
# runs it itself, through the shell prefix (`shell`), so its tasks, their
# controls and their notifications are the build's own.
NATIVE_TOOLS = (
    "Read",
    "Edit",
    "Write",
    "NotebookEdit",
)
PRIVATE_INSTRUCTIONS = (
    "This chat has 64 MiB of temporary scratch space at /work. "
    "Use shell and file tools for drafts and small processing tasks. "
    "Save finished documents through cheese_doc(action=set) and publish artifacts "
    "through cheese show. Scratch files can disappear when execution "
    "is released; they are not permanent storage. No project checkout is mounted."
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
    runner, or one that does not answer, ends as it would have. The runner
    is handed the text of the turn's final message, which the build puts in
    the hook's input (`last_assistant_message`, absent when it has none).
    It is asked again after a turn it held (`stop_hook_active`): it holds a
    turn for each of its two reasons at most once, and says when it is done."""
    path = os.environ.get(SESSION_SOCKET)
    if not path:
        return None
    ending = {"last_words": str(payload.get("last_assistant_message") or "")}
    try:
        with dial_runner(path, 10) as connection:
            connection.sendall(
                json.dumps({"method": "reply_check", "params": ending}).encode() + b"\n"
            )
            answer = json.loads(connection.makefile("rb").readline())
    except (OSError, ValueError):
        return None
    reason = (answer.get("result") or {}).get("reason")
    return {"decision": "block", "reason": reason} if reason else None


def checkpoint_turn() -> str | None:
    """The work id of the turn whose checkpoint this is: the runner's answer on
    the socket it gave this session. None when there is no runner to ask, which
    ends with the sync reported as a platform line instead of a turn's step."""
    path = os.environ.get(SESSION_SOCKET)
    if not path:
        return None
    try:
        with dial_runner(path, 10) as connection:
            connection.sendall(
                json.dumps({"method": "checkpoint_turn", "params": {}}).encode() + b"\n"
            )
            answer = json.loads(connection.makefile("rb").readline())
    except (OSError, ValueError):
        return None
    work = (answer.get("result") or {}).get("work_id")
    return str(work) if work else None


def cheese_source() -> Path:
    """The platform tool table: shipped beside this helper, or the checkout's."""
    shipped = Path(__file__).with_name("cheese.py")
    if shipped.is_file():
        return shipped
    return Path(__file__).resolve().parents[6] / "sandbox/cheese"


# `WORKING_DIRECTORY` — the fixed name this session's working directory also
# goes by — is imported from `executor_transport`: the name and the way its value
# crosses to the executor are one thing to that module, not a second declaration
# here. The line is told about it in its prompt
# (`harness.prompt.WORKING_DIRECTORY`), and the shell prefix binds it in the
# command's own shell (`shell_prefix.bind_working_directory`): it is set on this
# process so the prefix can read it, and deliberately not exported to the
# executor, where a native session has no such variable.


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
        if __package__:
            # deferred-import: private scratch requires fcntl, absent on Windows
            from .private import ensure, webfetch_egress
        else:
            # deferred-import: private scratch requires fcntl, absent on Windows
            from private import ensure, webfetch_egress
        ensure(target, directory, os.environ)
    if target.get("kind") == "deferred" and target["workspace"] != DEFERRED_WORKSPACE:
        target = _take_leased_machine(target)
    unavailable = target.get("kind") in {"unavailable", "deferred"}
    forwarded = target.get("kind") not in {"private", "unavailable"}
    device_forwarded = target.get("kind") in {"device", "deferred"}
    # A member's own Claude Code on their own computer (`owner_provider`): no
    # view, no namespace. It sees the project where an executor on this machine
    # holds it; before its lease, and with the executor elsewhere (`apart`), in
    # a directory of its own (`context_copy`), its placeholder here.
    local = bool(target.get("local"))
    apart = local and bool(target.get("apart"))
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
    at_placeholder = local and (unavailable or apart)
    if local:
        seen = placeholder if at_placeholder else info["workspace"]
        if not at_placeholder:
            workspace = Path(info["workspace"])
    # Where the session starts — and what `$T` stands for. A task's session
    # starts in its own checkout, which is only opened once the machine that
    # holds the project is at hand: a placeholder session keeps the project
    # view (there is no checkout to open yet), and takes its checkout when the
    # platform relaunches it onto the machine (`_take_leased_machine`).
    start = seen
    if (
        not unavailable
        and not at_placeholder
        and target.get("kind") != "private"
        and os.environ.get("CHEESE_TOPIC")
    ):
        working = client.working_directory()
        if working and working != info["workspace"]:
            start = working if local else session_path(working)
    # Taking the machine's hands (`acquire`) keeps the credential beside the
    # target when there is no token file yet; it must not travel inside the
    # target this writes, nor into the plugin built from it.
    target.pop("execution_token", None)
    carry_conversation(config, extra_args, start)
    target = dict(
        target,
        workspace=info["workspace"],
        session_workspace=seen,
        # Where the session itself starts, when that is not where it sees the
        # project: a task's session starts in its checkout (`enter` makes the
        # directory there before it chdirs, so the view stays where it was).
        start_directory=start,
        # Where its paths are rewritten from once it takes its machine
        # (`RemoteClient.call`): the directory it started in, not the
        # placeholder a session in its own namespace sees.
        **({"virtual_workspace": placeholder} if at_placeholder else {}),
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
        # The seat's memory tree, beside its execution config on this host:
        # the runner lays the platform's copy down and collects the agent's
        # edits there (`runner.memory_root`, from the same file), so its file
        # tools reach it here and not on the executor (`proxy.js`
        # `memoryPath`). Not in the room's home, which the room's seats share:
        # two seats can be working for two people at once.
        central_memory=str(directory / "memory"),
        # What the session's namespace leaves it of the room's seats and of the
        # session host's rooms (`namespace._keep_only`): its own seat, which
        # holds its memory tree, and its own room's home. The launch names
        # both roots (`device_launch`); a session it did not launch has none.
        seat_only=[
            [os.environ[root], str(own)]
            for root, own in (
                ("CHEESE_SEATS_ROOT", directory.parent),
                ("CHEESE_HOMES_ROOT", home),
            )
            if os.environ.get(root) and not local
        ],
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
    if forwarded and not local:
        failure = take_view(directory, target_path, workspace)
        if failure is not None:
            raise RuntimeError("Forwarded project mount failed: " + failure)
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
    settings.update(
        skipDangerousModePermissionPrompt=True,
        enableArtifact=False,
        attribution={**settings.get("attribution", {}), "sessionUrl": False},
    )
    settings_path = directory / "settings.json"
    settings_path.write_text(json.dumps(settings))
    write_claude_state(config, start)
    servers = {
        "native": {
            "type": "stdio",
            "command": helper[0],
            "args": [*helper[1:], "transport", str(target_path)],
        }
    }
    # The project's servers — the machine's commands, the type's and the remote
    # ones — are Claude Code's own HTTP servers, all at this session's gateway
    # (`with_gateway`), which sends each call where the server is served. The
    # address and the secret are the gateway's, known only once it listens,
    # so the file names them by the variables the launch sets.
    for name in session_servers(target):
        if name == "native":
            raise ValueError("MCP server name native is reserved for file operations")
        servers[name] = {
            "type": "http",
            "url": f"http://127.0.0.1:${{{PORT_ENV}}}/mcp/{quote(name, safe='')}",
            "headers": {SECRET_HEADER: f"${{{SECRET_ENV}}}"},
        }
    (directory / "mcp.json").write_text(json.dumps({"mcpServers": servers}))
    env = {
        "HOME": str(home),
        # The same short name in every session, and this session's own project
        # under it: what the line can type instead of the path it would
        # otherwise spell out in every Bash command. It stays on this host — the
        # shell prefix reads it here and binds it in each command's shell
        # (`shell_prefix.bind_working_directory`); the executor's environment is
        # what a native session there would have, with no such variable.
        WORKING_DIRECTORY: start,
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
        "MCP_TIMEOUT": str(STARTUP_S * 1000),
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
        # WebSearch runs at the model's provider, so it opens no route here.
        # WebFetch reads the page from this host, so it is let in only where the
        # transport preload will send that read through the chat's egress proxy;
        # without the preload it would leave by the host's own network.
        refused = ["Agent", "Task", "EnterWorktree", "ExitWorktree"]
        if PAGE_TRANSPORT in os.environ.get("BUN_OPTIONS", ""):
            env["CHEESE_WEBFETCH_EGRESS"] = webfetch_egress()
        else:
            refused.append("WebFetch")
        command.extend(["--disallowedTools", ",".join(refused)])
    launch = {
        # The session enters its own namespace first, where the project is at
        # `workspace`; `cwd` is where this host holds it, for starting there.
        "command": command if local else [*helper, "enter", str(target_path), *command],
        "env": env,
        "cwd": start if local else str(workspace),
        "workspace": start,
        "execution": str(target_path),
    }
    (directory / "launch.json").write_text(json.dumps(launch))
    return launch


def _take_leased_machine(target):
    """A session whose machine was leased before it started starts on it.

    The platform names where the machine holds the project (`central_provider`)
    only when the machine answered this turn, so the session is relaunched onto
    it then, and here takes the machine the way a placeholder session's first
    command does: through the lease, with the project's instructions, hooks and
    MCP servers. The turn is not held for a machine being prepared: a lease not
    to be had now starts it at the placeholder, as when its machine did not
    answer, and its first project tool takes the machine."""
    client = RemoteClient(dict(target))
    if client.take_now():
        return client.config
    return dict(target, workspace=DEFERRED_WORKSPACE)


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
        if target.get("apart") and "central_workspace" in target:
            copy_context(target, tree, RemoteClient(target))
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
    # The shell can replace even the executor's own files and responses. Keep
    # executable central configuration independent of anything it returns.
    snapshot = {
        "files": {},
        "instructions": MACHINE_OUT_OF_REACH
        if target.get("kind") == "unavailable"
        else PRIVATE_INSTRUCTIONS,
    }
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
    except Exception as error:  # the call it followed succeeded
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
    # （`cheese_card(action=request)` 推提交）走这个出口，于是也吃这个闸：当场说够不着，
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

    def compact_session() -> str:
        """Ask this session's runner to compact its context (`/compact`).

        The runner is this process's parent's runner, reached at the socket it
        handed the session — the same door `register_project_hooks` and the
        Stop hook use. The runner writes the command; it does not wait for it,
        because a compaction of a long conversation runs for minutes."""
        path = os.environ.get(SESSION_SOCKET)
        if not path:
            # A session built without its socket: an invariant of the launch.
            raise RuntimeError(  # i18n-exempt: launch invariant, never shown
                "This session has no runner to ask for a compaction"
            )
        with dial_runner(path, RUNNER_REQUEST_S) as connection:
            connection.sendall(
                json.dumps({"method": "compact", "params": {}}).encode() + b"\n"
            )
            answer = json.loads(connection.makefile("rb").readline())
        if "error" in answer:
            raise RuntimeError(str(answer["error"]))
        return "already" if (answer.get("result") or {}).get("already") else "requested"

    def current_run() -> str | None:
        """Which run of this session a platform call is made in, as its
        runner names it (`runner.Runner.dispatch` ``run``), at the same door
        `compact_session` uses."""
        path = os.environ.get(SESSION_SOCKET)
        if not path:
            # A session built without its socket: an invariant of the launch.
            raise RuntimeError(  # i18n-exempt: launch invariant, never shown
                "This session has no runner to ask which run it is in"
            )
        with dial_runner(path, RUNNER_REQUEST_S) as connection:
            connection.sendall(
                json.dumps({"method": "run", "params": {}}).encode() + b"\n"
            )
            answer = json.loads(connection.makefile("rb").readline())
        if "error" in answer:
            raise RuntimeError(str(answer["error"]))
        return (answer.get("result") or {}).get("run")

    def platform_tool(tool, args, call_id, invoke):
        host = PlatformHost(
            client, invoke, call_id, doc_versions, current_run, compact_session
        )
        try:
            text = cheese.run_platform_tool(tool, args, host)
        except Exception as exc:  # the agent reads the reason
            return {"error": failure_text(exc)}
        # Pictures the tool hands back (a mini-app's, as the platform ran it)
        # travel as image blocks after the text, for `proxy.js` to pass on.
        return {
            "value": {"stdout": str(text), "stderr": ""},
            "images": list(getattr(text, "images", ())),
        }

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
                    # What this session's deployment offers: a tool behind a
                    # switch it has not turned on is not on the list.
                    + cheese.offered_tools()
                }
            elif method == "tools/call":
                tool = request["params"]["name"]
                if tool not in (
                    "invoke",
                    "permission",
                    "send_user_file",
                ) and (tool not in cheese.PLATFORM_TOOLS):
                    raise ValueError("Unknown transport tool")
                payload = request["params"]["arguments"]
                # The arguments each of these takes, from the call's.
                picked = {
                    "chat_send": (
                        "content",
                        "reply_to",
                        "attachments",
                        "request_id",
                    ),
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

                if tool == "chat_send":
                    if args.get("attachments"):
                        args = chat_send_attachments(
                            client, config, args, invoke, payload
                        )
                    receipt = client.publish_message(payload, args)
                elif tool == "send_user_file":
                    receipt = deliver_send_user_file(
                        client, config, payload, args, invoke
                    )
                elif tool in cheese.PLATFORM_TOOLS:
                    receipt = platform_tool(tool, args, payload["id"], invoke)
                else:
                    receipt = (
                        client.permission(payload, args, notice)
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
                value = tool_answer(
                    outcome,
                    Path(target_path).parent / "tool-results",
                    receipt.get("images", ()),
                )
            elif method == "ping":
                value = {}
            else:
                raise ValueError("Unknown transport method")
            response = {"jsonrpc": "2.0", "id": request["id"], "result": value}
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "error": {"code": -32000, "message": failure_text(exc)},
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
    out; it is admitted only when it is the very file that path names inside
    the tree. Nothing that resolves outside it, and nothing reached through a
    link inside it: whose memory a call touches is read off the path it names
    (`memory.files.private_owner`), so a `team/` path that leads into somebody's
    `private/` would hand that person's memory to whoever reads the call."""
    if call.get("tool_name") not in NATIVE_TOOLS or not config.get("central_memory"):
        return False
    tool_input = call.get("tool_input") or {}
    path = str(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
    root = os.path.normpath(config["central_memory"])
    named = os.path.normpath(path)
    if not named.startswith(root + os.sep):
        return False
    real_root = os.path.realpath(root)
    return os.path.realpath(named) == os.path.join(
        real_root, os.path.relpath(named, root)
    )


def gateway(config):
    """Serve this session's MCP servers to its Claude Code (`mcp_gateway`).

    Says where it listens on its first line, then lives as long as the process
    that started it: the launch execs Claude Code in its place, so that is the
    session, and a gateway outliving it would hold a port nobody reads."""
    client = RemoteClient(config)
    door = Gateway(
        client.call,
        lambda: session_servers(client.config),
        observe=client.local_outcome,
        on_close=client.local_closed,
    )
    door.start()
    print(json.dumps({"port": door.port, "secret": door.secret}), flush=True)
    parent = os.getppid()
    while os.getppid() == parent:
        time.sleep(1)
    door.close()


def start_gateway(target_path, env):
    """Start the gateway the launch's `mcp.json` names: the launch's
    environment with its address and secret, and its process (None for a
    session with no project server). The process ends when its parent does."""
    if not session_servers(json.loads(Path(target_path).read_text())):
        return env, None
    started = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "gateway", str(target_path)],
        stdout=subprocess.PIPE,
        env=env,
    )
    assert started.stdout is not None
    line = started.stdout.readline()
    started.stdout.close()
    if not line:
        started.wait()
        # i18n-exempt: the launch's own failure, read in the runner's log
        raise RuntimeError("The MCP gateway did not start")
    where = json.loads(line)
    return {
        **env,
        PORT_ENV: str(where["port"]),
        SECRET_ENV: where["secret"],
    }, started


def with_gateway(target_path, env):
    """The launch's environment, with its gateway started. The launch execs
    Claude Code in this process's place, which so becomes the gateway's parent."""
    return start_gateway(target_path, env)[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=[
            "prepare",
            "launch",
            "gateway",
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
            # deferred-import: private scratch requires fcntl, absent on Windows
            from .private import release
        else:
            # deferred-import: private scratch requires fcntl, absent on Windows
            from private import release
        release(config)
    elif args.mode == "gateway":
        gateway(config)
    elif args.mode == "checkpoint":
        payload = json.load(sys.stdin)
        transcript = Path(payload["transcript_path"])
        identifier = hashlib.sha256(
            (
                json.dumps(payload, sort_keys=True) + str(transcript.stat().st_size)
            ).encode()
        ).hexdigest()
        result = RemoteClient(config).checkpoint(
            "checkpoint-" + identifier, turn=checkpoint_turn()
        )
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
        run_in_place(
            launch["command"],
            with_gateway(launch["execution"], dict(os.environ, **launch["env"])),
        )
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
        env = with_gateway(config["execution"], dict(os.environ, **config["env"]))
        os.chdir(config["cwd"])
        run_in_place(config["command"], env)


if __name__ == "__main__":
    main()
