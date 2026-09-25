"""Native child selection reaches admission through the installed harness."""

import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code import device_launch
from tests.pinned_claude import claude_binary


@pytest.mark.parametrize(
    "child_default,agent_type",
    [
        ("claude-sonnet-5", "general-purpose"),
        ("removed-child-model", "general-purpose"),
        ("claude-sonnet-5", "fork"),
    ],
)
def test_native_child_default_and_explicit_model_reach_admission(
    tmp_path, child_default, agent_type
):
    requests = []
    spawned = False

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            nonlocal spawned
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"headers": dict(self.headers), "body": body})
            if "/messages" not in self.path or "count_tokens" in self.path:
                self.reply(b'{"input_tokens":10}', "application/json")
                return
            tools = {tool["name"] for tool in body.get("tools", [])}
            content = [{"type": "text", "text": "fixture complete"}]
            stop = "end_turn"
            if "Agent" in tools and not spawned:
                spawned = True
                content = [
                    {
                        "type": "tool_use",
                        "id": f"toolu_{label}",
                        "name": "Agent",
                        "input": {
                            "description": label,
                            "prompt": f"Reply {label} fixture only",
                            "subagent_type": agent_type,
                            **({"model": model} if model else {}),
                        },
                    }
                    for label, model in (
                        ("default", None),
                        ("explicit", "sonnet" if agent_type == "fork" else "opus"),
                    )
                ]
                stop = "tool_use"
            events = [
                {
                    "type": "message_start",
                    "message": {
                        "id": f"msg_{len(requests)}",
                        "type": "message",
                        "role": "assistant",
                        "model": body["model"],
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 10, "output_tokens": 0},
                    },
                }
            ]
            for index, block in enumerate(content):
                if block["type"] == "tool_use":
                    delta = {
                        "type": "input_json_delta",
                        "partial_json": json.dumps(block["input"]),
                    }
                    block["input"] = {}
                else:
                    delta = {"type": "text_delta", "text": block["text"]}
                    block["text"] = ""
                events.extend(
                    [
                        {
                            "type": "content_block_start",
                            "index": index,
                            "content_block": block,
                        },
                        {"type": "content_block_delta", "index": index, "delta": delta},
                        {"type": "content_block_stop", "index": index},
                    ]
                )
            events.extend(
                [
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": stop, "stop_sequence": None},
                        "usage": {"output_tokens": 2},
                    },
                    {"type": "message_stop"},
                ]
            )
            self.reply(
                "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in events
                ).encode(),
                "text/event-stream",
            )

        def reply(self, data, content_type):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    transport = Path(device_launch.__file__).with_name("webfetch_transport.cjs")
    try:
        result = subprocess.run(
            [
                claude_binary(),
                "--print",
                "--model",
                "opus",
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
                "--strict-mcp-config",
                "--setting-sources",
                "user",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--",
                "Spawn both fixture children and collect their replies.",
            ],
            cwd=workspace,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
                "CLAUDE_CODE_SUBAGENT_MODEL": child_default,
                "BUN_OPTIONS": f"--preload={transport}",
            },
            capture_output=True,
            timeout=60,
        )
        (tmp_path / "runner.log").write_bytes(result.stdout + b"\n" + result.stderr)
        assert result.returncode == 0, result.stderr.decode()
        summary = json.loads(result.stdout)
        assert summary["subagent_stats"]["spawned"] == (
            0 if agent_type == "fork" else 2
        ), summary
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))
    children = [
        item
        for item in requests
        if item["headers"].get("x-claude-code-request-class") == "subagent"
    ]
    if agent_type == "fork":
        assert not children
        errors = [
            block["content"]
            for item in requests
            for message in item["body"].get("messages", [])
            for block in message.get("content", [])
            if isinstance(block, dict)
            and block.get("type") == "tool_result"
            and block.get("is_error")
        ]
        assert errors and all(
            "Agent type 'fork' not found" in error for error in errors
        )
        model_schema = next(
            tool["input_schema"]["properties"]["model"]
            for tool in requests[0]["body"]["tools"]
            if tool["name"] == "Agent"
        )
        assert "forks always inherit the parent model" in model_schema["description"]
        return
    assert len(children) == 2
    assert {child["body"]["model"] for child in children} == {
        child_default,
        "claude-opus-5",
    }
    for child in children:
        assert child["headers"]["x-cheese-child-model"] == child["body"]["model"]
    assert all(
        "x-cheese-child-model" not in item["headers"]
        for item in requests
        if item not in children
    )
