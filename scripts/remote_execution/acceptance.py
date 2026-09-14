"""Run the actual interactive Claude Code against a separate executor.

Each run retains model requests, terminal output and assertions under tmp/.
Use --ssh and --remote-root to repeat the same cases on another physical host.
"""

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from model_fixture import Handler, Server, dump, log
from rc_fixture import RemoteControlFixture

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/app/domain/agent/harness/claude_code/remote_execution"
spec = importlib.util.spec_from_file_location("execution_client", SOURCE / "client.py")
client = importlib.util.module_from_spec(spec)
spec.loader.exec_module(client)


def run(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, **kwargs)
    if result.returncode:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {shlex.join(command)}\n{result.stderr}"
        )
    return result.stdout


def remote_command(options, command):
    return (
        ["ssh", "-T", "-o", "BatchMode=yes", options.ssh, shlex.join(command)]
        if options.ssh
        else command
    )


def setup(folder, options):
    remote = (
        str(Path(options.remote_root) / folder.name)
        if options.ssh
        else str(folder / "execution")
    )
    run(remote_command(options, ["mkdir", "-p", remote]))
    for source in (
        SOURCE / "runtime.py",
        Path(__file__).parent / "custom_mcp.py",
        Path(__file__).parent / "seed.py",
    ):
        if options.ssh:
            run(
                [
                    "scp",
                    "-q",
                    str(source),
                    options.ssh + ":" + remote + "/" + source.name,
                ]
            )
        else:
            shutil.copyfile(source, Path(remote) / source.name)
    python = options.remote_python if options.ssh else sys.executable
    work = json.loads(
        run(remote_command(options, [python, remote + "/seed.py", remote]))
    )["workspace"]
    config = {
        "workspace": work,
        "claude": options.remote_claude if options.ssh else options.claude,
        "env": {"EXECUTION_ENV": "REMOTE_COMMAND_ENV"},
        "mcp_servers": {
            "custom": {
                "command": python,
                "args": [remote + "/custom_mcp.py"],
                "env": {"MCP_TEST_MARKER": "REMOTE_CUSTOM_ENV"},
            }
        },
    }
    target = {
        "command": [python, remote + "/runtime.py"],
        "state": remote + "/state",
        "mcp_servers": ["custom"],
    }
    if options.ssh:
        target["ssh"] = options.ssh
    executor = client.RemoteClient(target)
    run(executor.command("start"), input=json.dumps(config))
    dump(folder / "executor-input.json", config)
    return executor, target


def tool_results(body):
    return [
        block
        for message in body["messages"]
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    ]


def task_action(name, index):
    def action(body):
        content = next(
            block["content"]
            for block in tool_results(body)
            if block["tool_use_id"] == f"toolu_acceptance_{index}"
        )
        task_id = re.search(r"remote-[0-9a-f]{16}", json.dumps(content)).group()
        return {
            "name": name,
            "input": {
                "task_id": task_id,
                **({"block": True, "timeout": 5000} if name == "TaskOutput" else {}),
            },
        }

    return action


def read_background_output(body):
    result = next(
        r for r in tool_results(body) if r["tool_use_id"] == "toolu_acceptance_4"
    )
    path = re.search(
        r"Output is being written to: (.+?\.output)", result["content"]
    ).group(1)
    return {"name": "Read", "input": {"file_path": path}}


def case(folder, options):
    executor, target = setup(folder, options)
    tmux = ["tmux", "-L", "cheese-acceptance-" + folder.name]
    server = None
    try:
        launch = client.prepare(
            folder / "central",
            target,
            claude=options.claude,
            extra_args=[
                "--model",
                "claude-sonnet-4-6",
                "--tools",
                "Read,Edit,Write,Bash,TaskOutput,TaskStop,Skill",
                "--allowedTools",
                "Read,Edit,Write,Bash,TaskOutput,TaskStop,Skill,mcp__custom__echo,mcp__native__chat_send,mcp__native__platform_request",
                "--debug-file",
                str(folder / "claude-debug.log"),
            ],
        )
        execution_file = folder / "central/execution.json"
        if options.launcher == "device":
            center = folder / "device-work"
            center.mkdir(parents=True)
            launch = {"cwd": str(center), "env": {}}
            execution_file = (
                folder / "device-home/.claude/remote-session/execution.json"
            )
        center = Path(launch["cwd"])
        (center / "target.txt").write_text("CENTER_SENTINEL\n")
        actions = [
            {"name": "Read", "input": {"file_path": str(center / "target.txt")}},
            {
                "name": "Edit",
                "input": {
                    "file_path": str(center / "target.txt"),
                    "old_string": "BEFORE_EDIT",
                    "new_string": "AFTER_EDIT",
                },
            },
            {
                "name": "Write",
                "input": {
                    "file_path": str(center / "new.txt"),
                    "content": "REMOTE_WRITE",
                },
            },
            {
                "name": "Bash",
                "input": {"command": 'printf "%s" "$EXECUTION_ENV" > command.txt; pwd'},
            },
            {
                "name": "Bash",
                "input": {
                    "command": "sleep 0.1; printf REMOTE_BACKGROUND",
                    "run_in_background": True,
                },
            },
            task_action("TaskOutput", 4),
            {
                "name": "Bash",
                "input": {
                    "command": "sleep 2; printf WRONG > cancelled.txt",
                    "run_in_background": True,
                },
            },
            task_action("TaskStop", 6),
            {"name": "mcp__custom__echo", "input": {"message": "REMOTE_CUSTOM"}},
            {"name": "Skill", "input": {"skill": "remote-check"}},
            {"name": "Bash", "input": {"command": "rg --files -g '*.txt'"}},
            {
                "name": "Bash",
                "input": {"command": "rg AFTER_EDIT target.txt"},
            },
            read_background_output,
            {
                "name": "mcp__native__chat_send",
                "input": {
                    "content": "Published 'literally'\n$(touch forbidden-publication)"
                },
            },
            {"name": "mcp__native__platform_request", "input": {"method": "GET", "path": "/platform-fixture"}},
        ]
        if options.mode != "normal":
            actions = [actions[2]]
        rc = (
            RemoteControlFixture(
                folder,
                lambda event, **fields: log(
                    folder / "rc.jsonl", {"event": event, **fields}
                ),
            )
            if options.rc
            else None
        )
        server = Server(("127.0.0.1", 0), rc.handler(Handler) if rc else Handler)
        if rc:
            rc.base = f"http://127.0.0.1:{server.server_port}"
        server.state = {
            "dir": folder,
            "actions": actions,
            "requests": [],
            "claude_binary": options.claude,
        }
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "TERM", "LANG", "TMPDIR", "SHELL")
        }
        env.update(
            launch["env"],
            TERM="xterm-256color",
            ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
            ANTHROPIC_AUTH_TOKEN="fixture-no-real-credential",
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
            CHEESE_API=f"http://127.0.0.1:{server.server_port}",
            CHEESE_TOPIC="fixture",
            CHEESE_TOKEN="fixture-place-token",
        )
        if rc:
            for key in (
                "ANTHROPIC_BASE_URL",
                "ANTHROPIC_AUTH_TOKEN",
                "DISABLE_TELEMETRY",
                "DISABLE_ERROR_REPORTING",
            ):
                env.pop(key, None)
            env.update(
                HTTPS_PROXY=rc.base,
                NODE_EXTRA_CA_CERTS=str(rc.cert),
                CLAUDE_CODE_OAUTH_TOKEN="fake-rc-oauth-token",
                CLAUDE_CODE_OAUTH_SCOPES="user:inference user:profile user:sessions:claude_code",
                CLAUDE_CODE_SUBSCRIPTION_TYPE="max",
                NO_PROXY="127.0.0.1,localhost",
                no_proxy="127.0.0.1,localhost",
            )
            if options.launcher == "plugin":
                gate = Path(launch["env"]["CLAUDE_CONFIG_DIR"]) / ".claude.json"
                gates = json.loads(gate.read_text())
                gates.update(
                    cachedGrowthBookFeatures=rc.flags,
                    cachedGrowthBookFeaturesAt=int(time.time() * 1000),
                    oauthAccount={
                        "accountUuid": "00000000-0000-4000-8000-000000000001",
                        "emailAddress": "fixture@example.invalid",
                        "organizationUuid": "00000000-0000-4000-8000-000000000002",
                    },
                )
                dump(gate, gates)
                launch["command"].extend(
                    ["--remote-control", "Cheese isolated acceptance"]
                )
        if options.launcher == "device":
            sys.path.insert(0, str(ROOT / "backend"))
            from app.domain.agent.harness.claude_code.device_launch import (
                build_screen_launch,
            )
            from app.domain.agent.device_provider import DeviceChannel
            from tests.support.harness_prompts import system_prompt

            owner = folder / "device-owner"
            (owner / ".local/bin").mkdir(parents=True)
            (owner / ".local/bin/claude").symlink_to(options.claude)
            env["HOME"] = str(owner)
            if rc:
                env["CHEESE_REMOTE_CONTROL"] = "1"
            launch["command"], screen_env = build_screen_launch(
                hook_url=f"http://127.0.0.1:{server.server_port}/hook",
                hook_token="fixture-place-token",
                home_dir=str(folder / "device-home"),
                work_dir=str(folder / "device-work"),
                model="claude-sonnet-4-6",
                extra_env=env,
                execution_target=target,
                system_prompt=system_prompt(),
            )
            env.update(screen_env)

            class LocalDeviceHub:
                async def exec(self, device_id, command, *, stdin, timeout):
                    result = await asyncio.to_thread(
                        subprocess.run,
                        command,
                        input=stdin,
                        env=env,
                        text=True,
                        capture_output=True,
                        timeout=timeout,
                    )
                    return {
                        "exit": result.returncode,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                    }

            channel = object.__new__(DeviceChannel)
            channel._hub = LocalDeviceHub()
            launch["command"] = asyncio.run(
                channel._ship_launcher(
                    "fixture",
                    uuid.uuid4(),
                    launch["command"],
                    str(folder / "device-home"),
                )
            )
        if options.mode == "disabled":
            env["CLAUDE_CODE_ENABLE_FUNCTION_HOOKS"] = "0"
        elif options.mode == "throw":
            (folder / "central/plugin/hooks/proxy.js").write_text(
                'export function register(on) { on("tool.call", () => {throw new Error("fixture failure")}); }'
            )
        elif options.mode == "timeout":
            (folder / "central/plugin/hooks/proxy.js").write_text(
                'export function register(on) { on("tool.call", async () => {await new Promise(() => {})}); }'
            )
        elif options.mode == "disconnect":
            run(executor.command("stop"))
        launch["env"] = env
        dump(folder / "central/launch.json", launch)
        command = shlex.join(
            [
                sys.executable,
                str(SOURCE / "client.py"),
                "launch",
                str(folder / "central/launch.json"),
            ]
        )
        run(
            tmux
            + [
                "new-session",
                "-d",
                "-s",
                "agent",
                "-x",
                "120",
                "-y",
                "40",
                "-c",
                str(center),
                "sleep 300",
            ]
        )
        run(tmux + ["set-option", "-w", "-t", "agent", "remain-on-exit", "on"])
        run(tmux + ["respawn-pane", "-k", "-t", "agent", command])
        time.sleep(4)
        if rc:
            assert rc.connected.wait(25), run(
                tmux + ["capture-pane", "-p", "-t", "agent", "-S", "-100"]
            )
            rc.send(
                {
                    "type": "control_request",
                    "request_id": "initialize",
                    "request": {"subtype": "initialize"},
                }
            )
            rc.send(
                {
                    "type": "user",
                    "client_platform": "web_claude_ai",
                    "message": {
                        "role": "user",
                        "content": "Run the prescribed remote execution checks.",
                    },
                }
            )
        else:
            run(
                tmux
                + [
                    "send-keys",
                    "-t",
                    "agent",
                    "-l",
                    "Run the prescribed remote execution checks.",
                ]
            )
            run(tmux + ["send-keys", "-t", "agent", "Enter"])
        deadline = time.monotonic() + 90
        while (
            len(server.state["requests"]) < len(actions) + 1
            and not server.state.get("error")
            and time.monotonic() < deadline
        ):
            time.sleep(0.2)
        time.sleep(0.5)
        terminal = run(tmux + ["capture-pane", "-p", "-t", "agent", "-S", "-300"])
        (folder / "terminal.txt").write_text(terminal)
        assert len(server.state["requests"]) == len(actions) + 1, terminal
        assert (center / "target.txt").read_text() == "CENTER_SENTINEL\n"
        assert not (center / "new.txt").exists()
        results = tool_results(server.state["requests"][-1])
        if options.mode == "normal":
            assert not [r for r in results if r.get("is_error")], results
            assert "BEFORE_EDIT" in json.dumps(results[0])
            assert "REMOTE_BACKGROUND" in json.dumps(results[5]), results[5]
            assert "REMOTE_CUSTOM_ENV" in json.dumps(results[8]), results[8]
            assert "target.txt" in json.dumps(results[10]), results[10]
            assert "AFTER_EDIT" in json.dumps(results[11]), results[11]
            assert "REMOTE_BACKGROUND" in json.dumps(results[12]), results[12]
            publications = server.state.get("publications", [])
            assert len(publications) == 1, publications
            assert "PLATFORM_API_READ" in json.dumps(results), results
            assert (
                publications[0]["content"]
                == "Published 'literally'\n$(touch forbidden-publication)"
            )
            assert not (center / "forbidden-publication").exists()
            assert "REMOTE_PROJECT_INSTRUCTIONS" in json.dumps(
                server.state["requests"][0]
            )
            assert "REMOTE_SKILL_SENTINEL" in json.dumps(server.state["requests"][-1])
            assert "Environment: REMOTE_COMMAND_ENV" in json.dumps(
                server.state["requests"][-1]
            )
            assert "ACCEPTANCE_DONE" in terminal, terminal
            bound = client.RemoteClient(json.loads(execution_file.read_text()))
            preview = bound.control(
                {"subtype": "read_file", "path": str(center / "target.txt")}
            )
            assert preview["contents"] == "AFTER_EDIT\n", preview
            diff = bound.control({"subtype": "get_workspace_diff"})
            assert "+AFTER_EDIT" in diff["diff"]
            dump(folder / "rc-file-controls.json", {"preview": preview, "diff": diff})
            time.sleep(2.1)
            result = executor.call(
                "invoke",
                {
                    "id": "verify-cancel",
                    "tool": "Bash",
                    "args": {
                        "command": "test ! -e cancelled.txt && printf CANCEL_CONFIRMED"
                    },
                },
            )
            assert result["value"]["stdout"] == "CANCEL_CONFIRMED", result
            if options.launcher == "device":
                for request in server.state["requests"]:
                    text = "\n".join(
                        block.get("text", "") for block in request["system"]
                    )
                    assert text.count(system_prompt()) == 1, text
                deadline = time.monotonic() + 10
                hooks_file = folder / "hooks.jsonl"
                while not hooks_file.exists() and time.monotonic() < deadline:
                    time.sleep(0.1)
                hooks = [
                    json.loads(line)["payload"]
                    for line in hooks_file.read_text().splitlines()
                ]
                for event in ("PreToolUse", "PostToolUse"):
                    seen = [
                        h.get("tool_use_id")
                        for h in hooks
                        if h.get("hook_event_name") == event
                    ]
                    for index in range(8):
                        assert seen.count(f"toolu_acceptance_{index}") == 1, (
                            event,
                            seen,
                        )
                assert not [
                    h for h in hooks if h.get("tool_name") == "mcp__native__invoke"
                ], hooks
        else:
            assert results[0].get("is_error"), results
            if options.mode != "disconnect":
                check = executor.call(
                    "invoke",
                    {
                        "id": "verify-no-write",
                        "tool": "Bash",
                        "args": {"command": "test ! -e new.txt; printf '%s' $?"},
                    },
                )
                assert check["value"]["stdout"] == "0", check
        summary = {
            "mode": options.mode,
            "version": run([options.claude, "--version"]).strip(),
            "request_count": len(server.state["requests"]),
            "ssh": options.ssh,
            "launcher": options.launcher,
            "rc": options.rc,
            "central_unchanged": True,
            "passed": True,
        }
        dump(folder / "summary.json", summary)
        print(json.dumps(summary), flush=True)
    finally:
        subprocess.run(tmux + ["kill-server"], capture_output=True, timeout=10)
        subprocess.run(executor.command("stop"), capture_output=True, timeout=20)
        if server:
            server.shutdown()
            server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--launcher", choices=["plugin", "device"], default="plugin")
    parser.add_argument("--rc", action="store_true")
    parser.add_argument(
        "--mode",
        choices=["normal", "disabled", "throw", "timeout", "disconnect"],
        default="normal",
    )
    parser.add_argument("--ssh")
    parser.add_argument("--remote-root")
    parser.add_argument("--remote-python", default="python3")
    parser.add_argument("--remote-claude", default="claude")
    options = parser.parse_args()
    options.output = options.output.resolve()
    options.output.mkdir(parents=True)
    log(
        options.output / "progress.jsonl",
        {
            "item": options.mode,
            "status": "started",
            "input": vars(options) | {"output": str(options.output)},
        },
    )
    try:
        case(options.output, options)
    except Exception as exc:
        log(
            options.output / "progress.jsonl",
            {"item": options.mode, "status": "failed", "error": str(exc)},
        )
        raise
    else:
        log(
            options.output / "progress.jsonl",
            {"item": options.mode, "status": "completed"},
        )


if __name__ == "__main__":
    main()
