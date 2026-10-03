"""Run Claude Code, held by the runner as a room holds it, against a separate executor.

Each run retains model requests, the session's journal and assertions under tmp/.
Use --ssh and --remote-root to repeat the same cases on another physical host.
"""

import argparse
import asyncio
import base64
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

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "backend/app/domain/agent/harness/claude_code/remote_execution"
sys.path.insert(0, str(SOURCE))
import release as execution_release  # noqa: E402 — from the source tree above

sys.path.insert(0, str(ROOT / "backend"))
from tests.support import executor_release  # noqa: E402 — standard library only

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


def setup(folder, options, api):
    remote = (
        str(Path(options.remote_root) / folder.name)
        if options.ssh
        else str(folder / "execution")
    )
    run(remote_command(options, ["mkdir", "-p", remote + "/remote-execution"]))
    # The executor as a machine's bootstrap installs it; over ssh, installed
    # here and copied across file by file under the names it was installed as.
    installed = Path(remote) if not options.ssh else folder / "executor-release"
    executor_release.install(installed)
    sources = [
        (Path(__file__).parent / "custom_mcp.py", "custom_mcp.py"),
        (Path(__file__).parent / "seed.py", "seed.py"),
    ]
    if options.ssh:
        names = json.loads((installed / "executor-files.json").read_text())
        sources += [(installed / name, name) for name in names]
    for source, destination in sources:
        if options.ssh:
            run(
                [
                    "scp",
                    "-q",
                    str(source),
                    options.ssh + ":" + remote + "/" + destination,
                ]
            )
        else:
            shutil.copyfile(source, Path(remote) / destination)
    python = options.remote_python if options.ssh else sys.executable
    work = json.loads(
        run(remote_command(options, [python, remote + "/seed.py", remote]))
    )["workspace"]
    config = {
        "workspace": work,
        "claude": options.remote_claude if options.ssh else options.claude,
        "env": {
            "EXECUTION_ENV": "REMOTE_COMMAND_ENV",
            "CHEESE_API": api,
            "CHEESE_TOPIC": "fixture",
            "CHEESE_TOKEN": "fixture-place-token",
        },
        "mcp_servers": {
            "custom": {
                "command": python,
                "args": [remote + "/custom_mcp.py"],
                "env": {"MCP_TEST_MARKER": "REMOTE_CUSTOM_ENV"},
            }
        },
    }
    target = {
        "command": [python, remote + "/remote-execution/runtime.py"],
        "state": remote + "/state",
        "mcp_servers": ["custom"],
    }
    if options.ssh:
        target["ssh"] = options.ssh
    executor = client.RemoteClient(target)
    run(executor.command("start"), input=json.dumps(config))
    dump(folder / "executor-input.json", config)
    return executor, target


def is_notification(body):
    """A request whose last message is a background command's completion
    notice. Past the script, one of those is a turn the build started by
    itself, not a step of the script."""
    messages = body.get("messages") or []
    last = messages[-1] if messages else {}
    if last.get("role") != "user":
        return False
    content = last.get("content")
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return "<task-notification>" in content


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
        # The build's own background task: it names the id it gave the command.
        task_id = re.search(
            r"background with ID: ([A-Za-z0-9_-]+)\.", json.dumps(content)
        ).group(1)
        return {
            "name": name,
            "input": {"task_id": task_id},
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


def execution_handler():
    class DeviceExecutionHandler(Handler):
        def do_POST(self):
            if self.path != "/execution":
                return super().do_POST()
            assert self.headers.get("X-Cheese-Token") == "fixture-place-token"
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            wire = self.server.state["wire"]
            return self.reply(
                asyncio.run(
                    wire.call_executor(
                        "acceptance-machine",
                        self.server.state["executor_state"],
                        payload["method"],
                        payload.get("params", {}),
                    )
                )
            )

    return DeviceExecutionHandler


def before_turn(session, home):
    """What happens between the session starting and its first turn: nothing.

    `resident_release.py` puts a helper release in here.
    """


def case(folder, options):
    import runner_fixture

    server = Server(("127.0.0.1", 0), execution_handler())
    server.state = {
        "dir": folder,
        "actions": [],
        "requests": [],
        "claude_binary": options.claude,
    }
    threading.Thread(target=server.serve_forever, daemon=True).start()
    executor = None
    wire = None
    session = None
    center_fd = None
    try:
        executor, target = setup(
            folder, options, f"http://127.0.0.1:{server.server_port}"
        )
        # The session home, laid out as a room's is: the helpers under
        # `.cheese`, the target and everything the client prepares from it in
        # the SEAT that owns this session (`place.seat_dir`, `runner_fixture`),
        # and the config dir the room shares.
        home = folder / ("device-home" if options.launcher == "device" else "home")
        session_path = runner_fixture.session_dir(home)
        center = session_path / "remote-session/forwarded-project"
        center.mkdir(parents=True)
        # Held open from before the mount, so the directory beneath it can be
        # checked for writes that went there instead of to the executor.
        center_fd = os.open(center, os.O_RDONLY | os.O_DIRECTORY)
        execution_file = session_path / "remote-session/execution.json"
        # Where the session sees the project: at the executor's own path
        # (`client.py enter`); `center` is this host's view of it.
        seen = Path(executor.call("ping")["workspace"])
        actions = [
            {"name": "Read", "input": {"file_path": str(seen / "target.txt")}},
            {
                "name": "Edit",
                "input": {
                    "file_path": str(seen / "target.txt"),
                    "old_string": "BEFORE_EDIT",
                    "new_string": "AFTER_EDIT",
                },
            },
            {
                "name": "Write",
                "input": {
                    "file_path": str(seen / "new.txt"),
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
            {
                "name": "Bash",
                "input": {
                    "command": "sleep 2; printf WRONG > cancelled.txt",
                    "run_in_background": True,
                },
            },
            task_action("TaskStop", 5),
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
            # 这一步问的是「会话直接够得到平台吗」：`platform_request` 是会话侧
            # 的原始入口，不经过那台机器（结论 63）。
            {
                "name": "mcp__native__platform_request",
                "input": {"method": "GET", "path": "/platform-fixture"},
            },
            # A subagent that asks for isolation runs anyway, with its tools on
            # this host like every other one: the remote proxy drops the
            # parameter instead of refusing the spawn. What the session sees
            # here is the build's own async launch ack — an annotation the proxy
            # puts on *its* result does not survive that path, so the assertion
            # below reads the ack and not the annotation. The child's request is
            # the one request in this session that is not a turn of the script —
            # `is_child` in model_fixture.py answers it apart.
            {
                "name": "Agent",
                "input": {
                    "description": "Isolated look",
                    "prompt": "List the files.",
                    "isolation": "worktree",
                },
            },
            {"name": "Read", "input": {"file_path": str(seen / "image.png")}},
        ]
        if options.mode != "normal":
            actions = [actions[2]]
        server.state["actions"] = actions
        env = {
            k: v
            for k, v in os.environ.items()
            if k in ("PATH", "LANG", "TMPDIR", "SHELL")
        }
        env.update(
            ANTHROPIC_BASE_URL=f"http://127.0.0.1:{server.server_port}",
            ANTHROPIC_AUTH_TOKEN="fixture-no-real-credential",
            DISABLE_TELEMETRY="1",
            DISABLE_ERROR_REPORTING="1",
            CHEESE_API=f"http://127.0.0.1:{server.server_port}",
            CHEESE_TOPIC="fixture",
            CHEESE_TOKEN="fixture-place-token",
        )
        if options.launcher == "device":
            from app.domain.agent import machine_launcher
            from app.domain.agent.device_provider import DeviceChannel
            from app.domain.agent.harness.claude_code.session_launch import (
                ClaudeLaunch,
            )
            from app.domain.agent.harness.launch import MachinePlace
            from tests.support.harness_prompts import system_prompt
            from owner_fixture import WireOwner

            owner = folder / "device-owner"
            (owner / ".local/bin").mkdir(parents=True)
            (owner / ".local/bin/claude").symlink_to(options.claude)
            env["HOME"] = str(owner)
            wire = WireOwner(options.claude, exec_env=env)
            server.state.update(wire=wire, executor_state=target["state"])
            if not options.ssh:
                target = {
                    "kind": "device",
                    "url": f"http://127.0.0.1:{server.server_port}/execution",
                    "mcp_servers": target["mcp_servers"],
                }
            state = folder / "device-state"
            place = MachinePlace(
                home=str(home),
                workdir=str(folder / "device-work"),
                store="",
                state=str(state),
                api_base="",
                project_id="",
                topic_id="",
                agent_handle="",
                execution_target=target,
            )
            command, screen_env = machine_launcher.screen_launch(
                place,
                ClaudeLaunch(
                    system_prompt=system_prompt(), model="claude-sonnet-4-6"
                ).on(place),
                token="fixture-place-token",
            )
            env.update(screen_env)
            channel = object.__new__(DeviceChannel)
            channel._hub = wire
            command = asyncio.run(
                channel._ship_launcher(
                    "acceptance-machine",
                    uuid.uuid4(),
                    command,
                    str(home),
                    execution_token=env["CHEESE_TOKEN"],
                )
            )
            dump(folder / "launch.json", {"command": command, "env": env})
            # The launcher starts the runner itself, as a screen's does.
            launcher_log = folder / "launcher.log"
            launcher = subprocess.Popen(
                command,
                cwd=home,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=launcher_log.open("ab"),
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            session = runner_fixture.Session(state, launcher, launcher_log)
        else:
            env.update(runner_fixture.room_home(home, target))
            claude = options.claude
            if options.mode == "disabled":
                # `bootstrap` switches function hooks on for the session; the
                # fault is a build that starts with them off regardless.
                claude = folder / "claude-without-function-hooks"
                claude.write_text(
                    "#!/bin/sh\nCLAUDE_CODE_ENABLE_FUNCTION_HOOKS=0 exec "
                    + shlex.quote(options.claude)
                    + ' "$@"\n'
                )
                claude.chmod(0o755)
            elif options.mode in ("throw", "timeout"):
                # The helper `bootstrap` copies into the session's plugin.
                proxy = runner_fixture.session_dir(home) / "remote-execution/proxy.js"
                proxy.write_text(
                    'export function register(on) { on("tool.call", () => {throw new Error("fixture failure")}); }'
                    if options.mode == "throw"
                    else 'export function register(on) { on("tool.call", async () => {await new Promise(() => {})}); }'
                )
            command = runner_fixture.room_command(
                home,
                str(claude),
                [
                    "--model",
                    "claude-sonnet-4-6",
                    "--tools",
                    "Read,Edit,Write,Bash,TaskStop,Skill,Agent",
                    "--allowedTools",
                    "Read,Edit,Write,Bash,TaskStop,Skill,Agent,mcp__custom__echo,"
                    "mcp__native__chat_send,mcp__native__platform_request",
                    "--debug-file",
                    str(folder / "claude-debug.log"),
                ],
            )
            dump(folder / "launch.json", {"command": command, "env": env})
            session = runner_fixture.Session.start(folder, command, env, home)
            if options.mode == "disconnect":
                # Once `bootstrap` has prepared the session against it.
                prepared = (
                    runner_fixture.session_dir(home) / "remote-session/launch.json"
                )
                deadline = time.monotonic() + 60
                while not prepared.exists():
                    assert time.monotonic() < deadline, "bootstrap never prepared"
                    time.sleep(0.1)
                run(executor.command("stop"))
        before_turn(session, home)
        ended = session.turn("Run the prescribed remote execution checks.", 120)
        # The script's own turns, then the closing one. The build also starts a
        # turn of its own when a background command finishes
        # (`task-notification`), so the closing turn is not always the last
        # request: every request past it is that notice and nothing else.
        # `missing` below is what holds the script to running once, in order.
        rest = server.state["requests"][len(actions):]
        assert rest, ended
        assert all(is_notification(body) for body in rest[1:]), rest
        # The journal is what the room reads: every scripted call has its
        # tool_result there, on the session's own thread.
        ran = session.tool_results()
        missing = [
            index
            for index in range(len(actions))
            if f"toolu_acceptance_{index}" not in ran
        ]
        assert not missing, (missing, sorted(ran))
        for name in ("target.txt", "new.txt"):
            try:
                descriptor = os.open(name, os.O_RDONLY, dir_fd=center_fd)
            except FileNotFoundError:
                continue
            os.close(descriptor)
            raise AssertionError(
                f"Remote file tool modified the central workspace: {name}"
            )
        results = tool_results(server.state["requests"][-1])
        if options.mode == "normal":
            # The spawn that asked for isolation is no longer refused: the
            # proxy drops the parameter and the subagent runs like any other.
            # `cheese_task` is still what a caller should use when the work is
            # itself a deliverable to track and review, but nothing blocks the
            # spawn on it any more.
            isolated = results[-2]
            assert not isolated.get("is_error"), isolated
            # The spawn reached this gateway as a child of its own — the other
            # assertion no longer stopping it, and the script still ran once.
            assert len(server.state.get("child_requests", [])) >= 1, ended
            # `.claude/` itself is the project's mirrored assets; an isolation
            # that had taken effect would have added its worktree beneath it.
            assert not (center / ".claude/worktrees").exists()
            assert not [r for r in results[:-2] + results[-1:] if r.get("is_error")], (
                results
            )
            assert "BEFORE_EDIT" in json.dumps(results[0])
            picture = next(b for b in results[-1]["content"] if b["type"] == "image")
            original = executor.call(
                "invoke",
                {
                    "id": "verify-image",
                    "tool": "Read",
                    "args": {"file_path": "image.png"},
                },
            )
            assert picture["source"]["media_type"] == "image/png"
            assert len(base64.b64decode(picture["source"]["data"])) > 200_000
            assert picture["source"]["data"] == original["value"]["file"]["base64"]
            assert "REMOTE_CUSTOM_ENV" in json.dumps(results[7]), results[7]
            assert "target.txt" in json.dumps(results[9]), results[9]
            assert "AFTER_EDIT" in json.dumps(results[10]), results[10]
            assert "REMOTE_BACKGROUND" in json.dumps(results[11]), results[11]
            publications = server.state.get("publications", [])
            assert len(publications) == 1, publications
            assert "PLATFORM_API_READ" in json.dumps(results), results
            assert json.dumps(results).count("PLATFORM_API_READ") == 1, results
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
            assert ended.get("is_error") is False, ended
            assert "ACCEPTANCE_DONE" in (ended.get("result") or ""), ended
            bound = client.RemoteClient(json.loads(execution_file.read_text()))
            preview = bound.control(
                {"subtype": "read_file", "path": str(seen / "target.txt")}
            )
            assert preview["contents"] == "AFTER_EDIT\n", preview
            diff = bound.control({"subtype": "get_workspace_diff"})
            assert "+AFTER_EDIT" in diff["diff"]
            dump(folder / "file-controls.json", {"preview": preview, "diff": diff})
            # The stopped command would have written this after two seconds:
            # its absence is the command gone from the executor, not only the
            # build's task marked stopped.
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
        server.assert_healthy()
        summary = {
            "mode": options.mode,
            "version": run([options.claude, "--version"]).strip(),
            "request_count": len(server.state["requests"]),
            "ssh": options.ssh,
            "launcher": options.launcher,
            "central_unchanged": True,
            "passed": True,
        }
    finally:
        if center_fd is not None:
            os.close(center_fd)
        if session is not None:
            session.stop(folder / "journal.jsonl")
        for mountpoint in (
            *(
                runner_fixture.session_dir(folder / name)
                / "remote-session/forwarded-project"
                for name in ("home", "device-home")
            ),
            # The room-level pair a run under the layout before seats mounted,
            # so a machine that still carries one of those is cleared too.
            folder / "home/.cheese/remote-session/forwarded-project",
            folder / "device-home/.cheese/remote-session/forwarded-project",
        ):
            # `release_mount` rather than a local ismount-then-unmount: this runs
            # in a `finally`, so the run that most needs it is the one that got
            # here at all — and a run killed outright (this suite kills a case at
            # 300s) leaves a mount this block never sees. What clears THAT one is
            # the next run reaching this line, which only works if a dead mount
            # can be recognised, which `os.path.ismount` cannot do.
            assert execution_release.release_mount(mountpoint), mountpoint
        if executor is not None:
            subprocess.run(executor.command("stop"), capture_output=True, timeout=20)
        if wire is not None:
            wire.close()
        if server:
            server.shutdown()
            server.server_close()

    dump(folder / "summary.json", summary)
    print(json.dumps(summary), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--claude", default=shutil.which("claude"))
    parser.add_argument("--launcher", choices=["plugin", "device"], default="plugin")
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
