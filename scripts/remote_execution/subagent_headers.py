"""Verify the pinned CLI labels native subagent requests, without paid inference."""

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path

from model_fixture import Handler, Server


def main():
    root = Path(__file__).resolve().parents[2]
    folder = root / "tmp" / f"subagent-headers-{uuid.uuid4().hex[:8]}"
    folder.mkdir(parents=True)
    server = Server(("127.0.0.1", 0), Handler)
    server.state = {
        "dir": folder,
        "requests": [],
        "actions": [
            {
                "name": "Agent",
                "input": {
                    "description": "Verify child request",
                    "subagent_type": "general-purpose",
                    "prompt": "Reply with CHILD_DONE. Do not use tools.",
                },
            }
        ],
    }
    threading.Thread(target=server.serve_forever, daemon=True).start()
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ANTHROPIC_", "CLAUDE_", "CLAUDECODE"))
    }
    home = folder / "config"
    home.mkdir()
    env.update(
        {
            "CLAUDE_CONFIG_DIR": str(home),
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "ANTHROPIC_API_KEY": "fixture-not-a-credential",
            "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    binary = Path.home() / ".local/share/claude/versions/2.1.282"
    with (folder / "cli.log").open("w") as output:
        result = subprocess.run(
            [
                str(binary),
                "-p",
                "Use Agent to delegate a brief check, then finish.",
                "--model",
                "claude-sonnet-4-6",
                "--dangerously-skip-permissions",
                "--max-turns",
                "5",
                "--output-format",
                "json",
            ],
            cwd=folder,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            timeout=90,
        )
    server.shutdown()
    server.server_close()
    headers = [
        json.loads(path.read_text()) for path in sorted(folder.glob("headers-*.json"))
    ]
    classes = [
        {key.lower(): value for key, value in item.items()}.get(
            "x-claude-code-request-class"
        )
        for item in headers
    ]
    print(
        json.dumps(
            {"folder": str(folder), "exit_code": result.returncode, "classes": classes}
        )
    )
    assert "subagent" in classes, (
        "Native child request did not carry the routing header"
    )
    assert any(value != "subagent" for value in classes), (
        "No main-agent request recorded"
    )


if __name__ == "__main__":
    main()
