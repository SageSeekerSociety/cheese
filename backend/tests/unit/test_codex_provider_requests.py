"""Record model requests from the real Codex binary using a scripted local provider."""

import asyncio
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.agent.harness import Opening
from app.domain.agent.harness.codex import AppServer, Session
from tests.support.harness_prompts import event_prompts, system_prompt


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex binary required")
async def test_real_provider_receives_platform_prompt_and_matching_tool_result(
    tmp_path,
):
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(request)
            number = len(requests)
            if number == 1:
                item = {
                    "id": "fc_fixture",
                    "type": "function_call",
                    "call_id": "call_fixture",
                    "name": "cheese_fixture",
                    "arguments": '{"value":"fixture input"}',
                    "status": "completed",
                }
            else:
                item = {
                    "id": f"msg_fixture_{number}",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "fixture reply",
                            "annotations": [],
                        }
                    ],
                }
            response = {
                "id": f"resp_fixture_{number}",
                "object": "response",
                "status": "completed",
                "output": [item],
                "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {"type": "response.output_item.added", "output_index": 0, "item": item},
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
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
    (home / "config.toml").write_text(
        'model = "gpt-5.3-codex"\nmodel_provider = "fixture"\n'
        '[model_providers.fixture]\nname = "fixture"\n'
        f'base_url = "http://127.0.0.1:{provider.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        "[analytics]\nenabled = false\n"
    )
    prompt = system_prompt()
    inputs = list(event_prompts().values())
    completed = asyncio.Queue()
    calls = []
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    session = None

    async def on_event(event):
        if session is not None:
            session.observe(event)
        if event["method"] == "turn/completed":
            await completed.put(event["params"]["turn"])

    async def on_request(method, params):
        assert method == "item/tool/call"
        calls.append(params)
        tool_started.set()
        await release_tool.wait()
        return {
            "success": True,
            "contentItems": [{"type": "inputText", "text": "TOOL_RESULT_FIXTURE"}],
        }

    process = None
    listener = None

    async def connect(errors):
        nonlocal session
        process = await asyncio.create_subprocess_exec(
            "codex",
            "app-server",
            cwd=workspace,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CODEX_HOME": str(home),
            },
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=errors,
            limit=4 * 1024 * 1024,
        )
        assert process.stdout is not None and process.stdin is not None
        client = AppServer(
            process.stdout, process.stdin, on_event=on_event, on_request=on_request
        )
        session = Session(client)
        return process, client, asyncio.create_task(client.listen())

    try:
        with (tmp_path / "stderr.log").open("wb") as errors:
            process, client, listener = await connect(errors)
            async with asyncio.timeout(30):
                await client.initialize()
                thread_id = await session.open(
                    Opening(system_prompt=prompt),
                    cwd=str(workspace),
                    base_instructions="HARNESS_BASE_FIXTURE",
                    tools=[
                        {
                            "name": "cheese_fixture",
                            "description": "Contract fixture",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"value": {"type": "string"}},
                                "required": ["value"],
                                "additionalProperties": False,
                            },
                        }
                    ],
                )
                started = await session.send(inputs[0])
                await tool_started.wait()
                assert await session.send(inputs[1]) == started
                release_tool.set()
                assert (await completed.get())["status"] == "completed"
                for number, user_prompt in enumerate(inputs[2:], start=2):
                    if number == 2:
                        # Resume must recover history and dynamic tools from disk,
                        # without replaying the first user message or tool call.
                        process.terminate()
                        await process.wait()
                        await listener
                        process, client, listener = await connect(errors)
                        await client.initialize()
                        resumed = await session.open(
                            Opening(system_prompt=prompt, resume_token=thread_id),
                            cwd=str(workspace),
                            base_instructions="HARNESS_BASE_FIXTURE",
                            tools=[],
                        )
                        assert resumed == thread_id
                    await session.send(user_prompt)
                    assert (await completed.get())["status"] == "completed"
    finally:
        release_tool.set()
        if process is not None and process.returncode is None:
            process.terminate()
            await process.wait()
        if listener is not None:
            await listener
        provider.shutdown()
        provider.server_close()
        worker.join()
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))

    assert len(requests) == len(inputs)
    assert len(calls) == 1
    assert calls[0]["arguments"] == {"value": "fixture input"}
    for request in requests:
        assert request["instructions"] == "HARNESS_BASE_FIXTURE"
        platform = [
            part["text"]
            for item in request["input"]
            if item.get("role") == "developer"
            for part in item["content"]
            if part.get("text") == prompt
        ]
        assert platform == [prompt]
        tools = [
            tool for tool in request["tools"] if tool.get("name") == "cheese_fixture"
        ]
        assert len(tools) == 1
        assert tools[0]["parameters"] == {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        }
    tool_results = [
        item for item in requests[1]["input"] if item["type"] == "function_call_output"
    ]
    assert len(tool_results) == 1
    assert tool_results[0]["call_id"] == "call_fixture"
    assert "TOOL_RESULT_FIXTURE" in json.dumps(tool_results[0]["output"])
    for number, user_prompt in enumerate(inputs):
        request = requests[number]
        user_messages = [
            item for item in request["input"] if item.get("role") == "user"
        ]
        assert user_messages[-1]["content"] == [
            {"type": "input_text", "text": user_prompt}
        ]
    recorded_inputs = [
        part["text"]
        for item in requests[-1]["input"]
        if item.get("role") == "user"
        for part in item["content"]
        if part.get("text") in inputs
    ]
    assert recorded_inputs == inputs
