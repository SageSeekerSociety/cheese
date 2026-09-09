"""Drive two private chat turns through native Claude Code and RC."""

import argparse
import json
import os
import shlex
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
sys.path.insert(0, str(SOURCE))
from client import RemoteClient, prepare  # noqa: E402
from private import release, target  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    folder = args.output.resolve()
    folder.mkdir(parents=True, exist_ok=False)
    config = target(uuid.uuid4())
    tmux = ["tmux", "-L", "private-" + uuid.uuid4().hex[:12]]
    rc = RemoteControlFixture(
        folder,
        lambda event, **fields: log(folder / "rc.jsonl", {"event": event, **fields}),
    )
    server = Server(("127.0.0.1", 0), rc.handler(Handler))
    rc.base = f"http://127.0.0.1:{server.server_port}"
    actions = [
        {
            "name": "Write",
            "input": {"file_path": "/work/draft.md", "content": "Private draft\n"},
        },
        {"name": "Read", "input": {"file_path": "/work/draft.md"}},
        {
            "name": "Edit",
            "input": {
                "file_path": "/work/draft.md",
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
        send("Prepare and revise a private document draft.")
        wait_requests(4)
        actions.extend(
            [
                None,
                {
                    "name": "Bash",
                    "input": {
                        "command": "python3 - <<'PY'\nfrom pathlib import Path\nassert Path('/work/draft.md').read_text() == 'Revised draft\\n'\nprint('CROSS_TURN_SHELL_OK')\nPY"
                    },
                },
            ]
        )
        send("Continue processing the draft from the last message.")
        wait_requests(6)
        assert "CROSS_TURN_SHELL_OK" in json.dumps(server.state["requests"][-1])
        assert not (Path(launch["cwd"]) / "draft.md").exists()
        assert (
            RemoteClient(config).control(
                {"subtype": "read_file", "path": "/work/draft.md"}
            )["contents"]
            == "Revised draft\n"
        )
        terminal()
        dump(
            folder / "summary.json",
            {
                "passed": True,
                "turns": 2,
                "model_requests": 6,
                "central_file_unchanged": True,
            },
        )
    finally:
        subprocess.run(tmux + ["kill-server"], capture_output=True)
        server.shutdown()
        server.server_close()
        release(config)


if __name__ == "__main__":
    main()
