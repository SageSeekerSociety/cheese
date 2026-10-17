"""Native child selection reaches admission through the installed harness."""

import asyncio
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.core.config import settings
from app.domain.agent import gateway_catalog
from app.domain.agent.gateway import LlmGateway
from app.domain.agent.gateway_usage import model_agents
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
        # What the pinned build resolves the `opus` alias to.
        "claude-opus-5-5",
    }
    # The child's resolved model has to reach admission. 2.1.283 moved the
    # harness's own model requests off the JS fetch path — a whole 2.1.299 turn
    # makes zero `fetch` calls — so the preload writes no `x-cheese-child-model`
    # any more, and the metering proxy takes its deferred road instead: a
    # subagent /v1/messages is buffered whole and the name is read off the body
    # (`test_metering_proxy_addon.py`, and the recordings under
    # tests/fixtures/provider-recordings/ hold both roads ending at one name).
    # What this test holds is the harness half: the request is labelled a child
    # and the name the parent gave is in the body, where the proxy reads it.
    for child in children:
        # What makes it a child request to the proxy, and so eligible for the
        # deferred road at all.
        assert child["headers"]["x-claude-code-agent-id"]
        # On the builds whose requests the preload still sees this header is
        # what carries the name; where it is written it has to agree with the
        # body. Absent is the truth on the pinned build, not a failure.
        named = child["headers"].get("x-cheese-child-model")
        assert named is None or named == child["body"]["model"]
    assert all(
        "x-cheese-child-model" not in item["headers"]
        for item in requests
        if item not in children
    )


def test_a_model_the_project_offers_is_a_subagent_the_session_can_start(
    tmp_path, monkeypatch
):
    """The Agent tool takes a model only as one of Claude's aliases, so a model
    from the gateway is named by starting the subagent type the launch wrote
    for it — and that subagent's requests go out on that model, from the
    session's first turn."""
    monkeypatch.setattr(settings, "agent_model", "glm-5.2")
    gateway_catalog.reset()
    try:
        asyncio.run(
            gateway_catalog.refresh(
                LlmGateway(
                    "http://gw",
                    "mk",
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(
                            200,
                            json={
                                "data": [
                                    {
                                        "model_name": name,
                                        "litellm_params": {"model": f"openai/{name}"},
                                        "model_info": {
                                            "cheese_selectable": True,
                                            "input_cost_per_token": 0.000001,
                                            "output_cost_per_token": 0.000001,
                                        },
                                    }
                                    for name in ("glm-5.2", "kimi-k3")
                                ]
                            },
                        )
                    ),
                )
            )
        )
        definitions = model_agents({})
    finally:
        gateway_catalog.reset()
    home = tmp_path / "home"
    agents = home / ".claude" / "agents"
    agents.mkdir(parents=True)
    for name, content in definitions.items():
        (agents / name).write_text(content)
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(
                {"path": self.path, "headers": dict(self.headers), "body": body}
            )
            if "/messages" not in self.path or "count_tokens" in self.path:
                self.reply(b'{"input_tokens":10}', "application/json")
                return
            child = bool(self.headers.get("x-claude-code-agent-id"))
            answered = any(
                isinstance(block, dict) and block.get("type") == "tool_result"
                for message in body.get("messages", [])
                if isinstance(message.get("content"), list)
                for block in message["content"]
            )
            if child or answered:
                block = {"type": "text", "text": "done"}
                delta = {"type": "text_delta", "text": "done"}
                stop = "end_turn"
            else:
                call = {
                    "description": "on kimi",
                    "prompt": "Reply done",
                    "subagent_type": "kimi-k3",
                    "run_in_background": False,
                }
                block = {"type": "tool_use", "id": "toolu_kimi", "name": "Agent"}
                delta = {"type": "input_json_delta", "partial_json": json.dumps(call)}
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
                },
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        **block,
                        **({"input": {}} if block["type"] == "tool_use" else {}),
                        **({"text": ""} if block["type"] == "text" else {}),
                    },
                },
                {"type": "content_block_delta", "index": 0, "delta": delta},
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": stop, "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
            ]
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
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
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
                "Run the fixture child on kimi.",
            ],
            cwd=workspace,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_SUBAGENT_MODEL": "glm-5.2",
            },
            capture_output=True,
            timeout=60,
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join()
    assert result.returncode == 0, result.stderr.decode()
    children = [
        item["body"]["model"]
        for item in requests
        if item["headers"].get("x-claude-code-agent-id")
        and "count_tokens" not in item["path"]
    ]
    assert children == ["kimi-k3"], requests[-1]["body"].get("messages", [])[-1:]
