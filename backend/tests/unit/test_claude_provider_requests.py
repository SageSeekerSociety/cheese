"""Verify prompt delivery at the Claude model boundary in isolated print mode.

Interactive session control has a separate contract; this tests the model request.
"""

import asyncio
import json
import os
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.pinned_claude import claude_binary
from tests.support.harness_prompts import event_prompts, system_prompt


@pytest.mark.anyio
@pytest.mark.parametrize("event_name", event_prompts())
async def test_real_claude_provider_receives_complete_platform_prompt(
    tmp_path, event_name
):
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/messages" not in self.path or "count_tokens" in self.path:
                data = b'{"input_tokens":10}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            requests.append(request)
            message = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": request["model"],
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            }
            events = [
                {"type": "message_start", "message": message},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": "fixture reply"},
                },
                {"type": "content_block_stop", "index": 0},
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                    "usage": {"output_tokens": 2},
                },
                {"type": "message_stop"},
            ]
            data = "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                for event in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    prompt = system_prompt()
    user_prompt = event_prompts()[event_name]
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            claude_binary(),
            "--print",
            "--model",
            "claude-sonnet-4-6",
            "--output-format",
            "json",
            "--append-system-prompt",
            prompt,
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "user",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--",
            user_prompt,
            cwd=workspace,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 30)
        assert process.returncode == 0, stderr.decode()
        result = json.loads(stdout)
        assert result["result"] == "fixture reply"
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        provider.shutdown()
        provider.server_close()
        worker.join()
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))
    carrying_prompt = [
        request
        for request in requests
        if any(
            # 2.1.265 appends its git snapshot after --append-system-prompt.
            block.get("text", "")
            .split("\n\ngitStatus:", 1)[0]
            .endswith("\n\n" + prompt)
            for block in request.get("system", [])
        )
    ]
    assert len(requests) == 1
    assert len(carrying_prompt) == 1
    request = carrying_prompt[0]
    system = "\n".join(block.get("text", "") for block in request["system"])
    assert system.count(prompt) == 1
    user = request["messages"][-1]
    assert user["role"] == "user"
    assert [
        block["text"] for block in user["content"] if block.get("text") == user_prompt
    ] == [user_prompt]


@pytest.mark.anyio
async def test_a_stream_that_breaks_is_never_sent_again_without_streaming(tmp_path):
    """A model route that only streams (a ChatGPT-subscription deployment
    behind the gateway) answers a non-streaming request with 400 "Stream must
    be set to true". Left to its default, the CLI answers a stream that breaks
    mid-response by sending the same request again without streaming, so a
    transient stream error became that 400 and ended the turn. With the
    session's settings, the stream's own error goes to the CLI's retry layer
    and no request leaves without streaming."""
    from app.domain.agent.harness.claude_code.session_launch import session_settings

    streamed: list[bool] = []

    class Provider(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            pass

        def _json(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/messages" not in self.path or "count_tokens" in self.path:
                return self._json(200, {"input_tokens": 10})
            streamed.append(bool(request.get("stream")))
            if not request.get("stream"):
                return self._json(400, {"detail": "Stream must be set to true"})
            message = {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": request["model"],
                "content": [],
                "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            }
            # The stream starts, then fails mid-response.
            events = [
                {"type": "message_start", "message": message},
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {"type": "text", "text": ""},
                },
                {
                    "type": "error",
                    "error": {"type": "api_error", "message": "upstream dropped"},
                },
            ]
            data = "".join(
                f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                for event in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text(
        json.dumps({"env": session_settings()["env"]})
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    process = None
    try:
        process = await asyncio.create_subprocess_exec(
            claude_binary(),
            "--print",
            "--model",
            "claude-sonnet-4-6",
            "--output-format",
            "json",
            "--tools",
            "",
            "--strict-mcp-config",
            "--setting-sources",
            "user",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--",
            "hello",
            cwd=workspace,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            stdin=subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), 60)
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        provider.shutdown()
        provider.server_close()
        worker.join()
    assert streamed and all(streamed), streamed
    assert "Stream must be set to true" not in stdout.decode()
