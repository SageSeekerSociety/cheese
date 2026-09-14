"""Drive central chat turns through native Claude Code and RC."""

import argparse
import json
import os
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
sys.path.insert(0, str(ROOT / "backend"))
from tests.support.harness_prompts import event_prompts, system_prompt  # noqa: E402

sys.path.insert(0, str(SOURCE))
from client import RemoteClient, prepare  # noqa: E402
from private import release, target  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ordinary", action="store_true")
    args = parser.parse_args()
    platform_system = system_prompt()
    platform_events = list(event_prompts().values())
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    prompt_file = folder / "system-prompt.md"
    prompt_file.write_text(platform_system)
    config = target(uuid.uuid4())
    room = None
    handler = Handler
    work = "/work"
    if args.ordinary:
        from room_fixture import RoomExecutor

        room = RoomExecutor(folder, args.claude)
        config = room.target
        work = str(room.work)
        os.environ["CHEESE_TOKEN"] = "room-fixture-token"
    tmux = ["tmux", "-L", "private-" + uuid.uuid4().hex[:12]]
    rc = RemoteControlFixture(
        folder,
        lambda event, **fields: log(folder / "rc.jsonl", {"event": event, **fields}),
    )
    handler = rc.handler(handler)
    if room:
        handler = room.handler(handler)
    server = Server(("127.0.0.1", 0), handler)
    rc.base = f"http://127.0.0.1:{server.server_port}"
    if room:
        config["url"] = rc.base + "/execution"
        room.set_api(rc.base)
    actions = [
        {
            "name": "Write",
            "input": {"file_path": work + "/draft.md", "content": "Private draft\n"},
        },
        {"name": "Read", "input": {"file_path": work + "/draft.md"}},
        {
            "name": "Edit",
            "input": {
                "file_path": work + "/draft.md",
                "old_string": "Private",
                "new_string": "Revised",
            },
        },
    ]
    server.state = {"dir": folder, "actions": actions, "requests": []}
    threading.Thread(target=server.serve_forever, daemon=True).start()

    def run(arguments):
        result = subprocess.run(
            tmux + arguments, text=True, capture_output=True, check=True
        )
        return result.stdout

    def terminal():
        text = run(["capture-pane", "-p", "-t", "agent", "-S", "-300"])
        (folder / "terminal.txt").write_text(text)
        return text

    def send(text):
        rc.send(
            {
                "type": "user",
                "client_platform": "web_claude_ai",
                "message": {"role": "user", "content": text},
            }
        )

    def wait_requests(count):
        deadline = time.monotonic() + 90
        while len(server.state["requests"]) < count and time.monotonic() < deadline:
            time.sleep(0.2)
        assert len(server.state["requests"]) == count, terminal()
        time.sleep(0.5)
        results = [
            block
            for message in server.state["requests"][-1]["messages"]
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if block.get("type") == "tool_result"
        ]
        assert results and not any(block.get("is_error") for block in results), results

    try:
        launch = prepare(
            folder / "central",
            config,
            claude=args.claude,
            extra_args=[
                "--dangerously-skip-permissions",
                "--remote-control",
                "Private acceptance",
                "--append-system-prompt-file",
                str(prompt_file),
            ],
        )
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
        env = {
            key: value
            for key, value in os.environ.items()
            if key in ("PATH", "TERM", "LANG", "SHELL", "DOCKER_HOST", "DOCKER_CONTEXT")
        }
        env.update(
            launch["env"],
            TERM="xterm-256color",
            HTTPS_PROXY=rc.base,
            NODE_EXTRA_CA_CERTS=str(rc.cert),
            CLAUDE_CODE_OAUTH_TOKEN="fake-rc-oauth-token",
            CLAUDE_CODE_OAUTH_SCOPES="user:inference user:profile user:sessions:claude_code",
            CLAUDE_CODE_SUBSCRIPTION_TYPE="max",
            NO_PROXY="127.0.0.1,localhost",
            no_proxy="127.0.0.1,localhost",
        )
        if room:
            env["CHEESE_TOKEN"] = "room-fixture-token"
        launch["env"] = env
        dump(folder / "launch.json", launch)
        run(["new-session", "-d", "-s", "agent", "-x", "120", "-y", "40", "sleep 300"])
        run(["set-option", "-w", "-t", "agent", "remain-on-exit", "on"])
        run(
            [
                "respawn-pane",
                "-k",
                "-t",
                "agent",
                shlex.join(
                    [
                        sys.executable,
                        str(SOURCE / "client.py"),
                        "launch",
                        str(folder / "launch.json"),
                    ]
                ),
            ]
        )
        assert rc.connected.wait(30), terminal()
        rc.send(
            {
                "type": "control_request",
                "request_id": "initialize",
                "request": {"subtype": "initialize"},
            }
        )
        send("Prepare and revise a private document draft.\n" + platform_events[0])
        wait_requests(4)
        actions.extend(
            [
                None,
                {
                    "name": "Bash",
                    "input": {
                        "command": "python3 - <<'PY'\nfrom pathlib import Path\nassert Path('draft.md').read_text() == 'Revised draft\\n'\nprint('CROSS_TURN_SHELL_OK')\nPY"
                    },
                },
            ]
        )
        if room:
            actions.append(
                {"name": "mcp__custom__echo", "input": {"message": "ROOM_CUSTOM_MCP"}}
            )
            actions.append(
                {"name": "Bash", "input": {"command": f"cheese worktree {room.task}"}}
            )
        send(
            "Continue processing the draft from the last message.\n"
            + platform_events[1]
        )
        wait_requests(8 if room else 6)
        assert "CROSS_TURN_SHELL_OK" in json.dumps(server.state["requests"][-1])
        assert not (Path(launch["cwd"]) / "draft.md").exists()
        assert (
            RemoteClient(config).control(
                {"subtype": "read_file", "path": work + "/draft.md"}
            )["contents"]
            == "Revised draft\n"
        )
        if room:
            assert (room.work / "custom.txt").read_text() == "ROOM_CUSTOM_MCP"
            assert (room.work / "setup-result").read_text() == "executor-env"
            assert not (room.work / "backend").exists()
            task_work = room.home / ".cheese/tasks" / room.task
            assert (task_work / "backend/startup-result").read_text() == "executor-env"
            assert not (Path(launch["cwd"]) / "custom.txt").exists()
            # Resume the original bytes under a different HOME and cwd, as a
            # session migration does. The chunked transfer has its own test.
            run(["send-keys", "-t", "agent", "-l", "/exit"])
            run(["send-keys", "-t", "agent", "Enter"])
            deadline = time.monotonic() + 20
            while (
                run(["display-message", "-p", "-t", "agent", "#{pane_dead}"]).strip()
                != "1"
            ):
                assert time.monotonic() < deadline, terminal()
                time.sleep(0.1)
            source = Path(launch["env"]["CLAUDE_CONFIG_DIR"]) / "projects"
            originals = {
                p.relative_to(source): p.read_bytes() for p in source.rglob("*.jsonl")
            }
            assert len(originals) == 1, list(originals)
            resume = next(iter(originals)).stem
            resumed = prepare(
                folder / "resumed-center",
                config,
                claude=args.claude,
                extra_args=[
                    "--dangerously-skip-permissions",
                    "--remote-control",
                    "Resumed acceptance",
                    "--resume",
                    resume,
                    "--append-system-prompt-file",
                    str(prompt_file),
                ],
            )
            resumed_config = Path(resumed["env"]["CLAUDE_CONFIG_DIR"])
            shutil.copytree(source, resumed_config / "projects")
            resumed_gate = json.loads((resumed_config / ".claude.json").read_text())
            resumed_gate.update(
                {
                    k: gates[k]
                    for k in (
                        "cachedGrowthBookFeatures",
                        "cachedGrowthBookFeaturesAt",
                        "oauthAccount",
                    )
                }
            )
            dump(resumed_config / ".claude.json", resumed_gate)
            resumed["env"] = {**env, **resumed["env"]}
            dump(folder / "resumed-launch.json", resumed)
            rc.connected.clear()
            run(
                [
                    "respawn-pane",
                    "-k",
                    "-t",
                    "agent",
                    shlex.join(
                        [
                            sys.executable,
                            str(SOURCE / "client.py"),
                            "launch",
                            str(folder / "resumed-launch.json"),
                        ]
                    ),
                ]
            )
            assert rc.connected.wait(30), terminal()
            actions.extend(
                [None, {"name": "Read", "input": {"file_path": work + "/draft.md"}}]
            )
            send(
                "Resume the same conversation and read the revised draft.\n"
                + platform_events[2]
            )
            wait_requests(10)
            history = json.dumps(server.state["requests"][-1]["messages"])
            assert "Prepare and revise a private document draft." in history
            assert "ROOM_CUSTOM_MCP" in history
            assert "Revised draft" in history
            assert not (Path(resumed["cwd"]) / "draft.md").exists()
            assert {
                p.relative_to(source): p.read_bytes() for p in source.rglob("*.jsonl")
            } == originals
        for event in platform_events[3 if room else 2 :]:
            count = len(server.state["requests"]) + 1
            send(event)
            wait_requests(count)
        requests = server.state["requests"]
        for request in requests:
            blocks = request["system"]
            system = "\n".join(block.get("text", "") for block in blocks)
            assert system.count(platform_system) == 1, system
            assert "Bash" in {tool["name"] for tool in request["tools"]}
        history = requests[-1]["messages"]
        user_text = "\n".join(
            message["content"]
            if isinstance(message["content"], str)
            else "\n".join(block.get("text", "") for block in message["content"])
            for message in history
            if message["role"] == "user"
        )
        for event in platform_events:
            assert event in user_text, event
        dump(folder / "provider-requests.json", requests)
        terminal()
        dump(
            folder / "summary.json",
            {
                "passed": True,
                "turns": len(platform_events),
                "platform_system_exact": True,
                "platform_events": list(event_prompts()),
                "model_requests": len(server.state["requests"]),
                "central_file_unchanged": True,
            },
        )
    finally:
        subprocess.run(tmux + ["kill-server"], capture_output=True)
        server.shutdown()
        server.server_close()
        if room:
            room.close()
        else:
            release(config)


if __name__ == "__main__":
    main()
