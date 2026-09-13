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
from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.harness.codex.runner import Runner, socket_path
from app.domain.agent.service import AgentMessage, AgentResult, AgentToolUse
from tests.support.harness_prompts import event_prompts, system_prompt


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex binary required")
@pytest.mark.parametrize("model", ["gpt-5.3-codex", "gpt-6-astra"])
async def test_real_provider_receives_platform_prompt_and_matching_tool_result(
    tmp_path,
    model,
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
                if model == "gpt-6-astra":
                    item = {
                        "id": "fc_fixture",
                        "type": "custom_tool_call",
                        "call_id": "call_fixture",
                        "name": "exec",
                        "namespace": "functions",
                        "input": (
                            "text(await tools.cheese_fixture("
                            '{value: "fixture input"}));'
                        ),
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
        f'model = "{model}"\nmodel_provider = "fixture"\n'
        '[model_providers.fixture]\nname = "fixture"\n'
        f'base_url = "http://127.0.0.1:{provider.server_port}/v1"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        "[analytics]\nenabled = false\n"
    )
    prompt = system_prompt()
    inputs = list(event_prompts().values())
    calls = []
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()

    async def on_request(method, params):
        assert method == "item/tool/call"
        calls.append(params)
        tool_started.set()
        await release_tool.wait()
        return {
            "success": True,
            "contentItems": [{"type": "inputText", "text": "TOOL_RESULT_FIXTURE"}],
        }

    state = home / "runner"
    runner = None
    cursor = 0
    observed = []

    async def rpc(method, params=None, *, abandon=False):
        reader, writer = await asyncio.open_unix_connection(
            socket_path(state), limit=4 * 1024 * 1024
        )
        try:
            writer.write(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            await writer.drain()
            if abandon:
                return None
            result = json.loads(await reader.readline())
            assert "error" not in result, result
            return result["result"]
        finally:
            writer.close()
            await writer.wait_closed()

    async def wait_completed():
        nonlocal cursor
        while True:
            entries = (await rpc("events", {"after": cursor}))["events"]
            for entry in entries:
                cursor = entry["sequence"]
                event = entry["record"]
                observed.append(event)
                if event["method"] == "turn/completed":
                    assert event["params"]["turn"]["status"] == "completed"
                    return
            await asyncio.sleep(0.005)

    async def connect(resume=None, *, tools):
        nonlocal runner
        runner = Runner(state, on_request)
        return await runner.start(
            Opening(system_prompt=prompt, resume_token=resume),
            binary=shutil.which("codex"),
            cwd=str(workspace),
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CODEX_HOME": str(home),
            },
            base_instructions="HARNESS_BASE_FIXTURE",
            tools=tools,
        )

    try:
        async with asyncio.timeout(30):
            thread_id = await connect(
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
                ]
            )
            contender = Runner(state, on_request)
            try:
                with pytest.raises(BlockingIOError):
                    await contender.start(
                        Opening("unused"),
                        binary="must-not-start",
                        cwd=str(workspace),
                        env={},
                        tools=[],
                    )
            finally:
                await contender.close()
            first = await rpc("send", {"input_id": "first", "text": inputs[0]})
            await tool_started.wait()
            await rpc("send", {"input_id": "steering", "text": inputs[1]}, abandon=True)
            retry = await rpc("send", {"input_id": "steering", "text": inputs[1]})
            assert retry["turn_id"] == first["turn_id"]
            release_tool.set()
            await wait_completed()
            before_restart = (await rpc("events"))["events"]
            await runner.close()
            assert await connect(thread_id, tools=[]) == thread_id
            after_restart = (await rpc("events"))["events"]
            assert after_restart[: len(before_restart)] == before_restart
            assert await rpc("send", {"input_id": "first", "text": inputs[0]}) == first
            for number, user_prompt in enumerate(inputs[2:], start=2):
                await rpc("send", {"input_id": str(number), "text": user_prompt})
                await wait_completed()
    finally:
        release_tool.set()
        if runner is not None:
            await runner.close()
        provider.shutdown()
        provider.server_close()
        worker.join()
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))

    assert len(requests) == len(inputs)
    assert len(calls) == 1
    assembler = Assembler()
    room_events = [event for record in observed for event in assembler.accept(record)]
    messages = [event for event in room_events if isinstance(event, AgentMessage)]
    assert len(messages) == len(inputs) - 1
    assert all(message.text == "fixture reply" for message in messages)
    assert len({message.eid for message in messages}) == len(messages)
    assert len([event for event in room_events if isinstance(event, AgentResult)]) == 5
    uses = [event for event in room_events if isinstance(event, AgentToolUse)]
    assert len(uses) == 1
    assert uses[0].input == {"value": "fixture input"}
    assert calls[0]["arguments"] == {"value": "fixture input"}
    for index, request in enumerate(requests):
        lite = model == "gpt-6-astra"
        if lite:
            assert "tools" not in request and "instructions" not in request
            declarations = [
                item for item in request["input"] if item["type"] == "additional_tools"
            ]
            assert len(declarations) == 1
            tool_specs = [
                tool
                for namespace in declarations[0]["tools"]
                for tool in namespace["tools"]
            ]
        else:
            assert request["instructions"] == "HARNESS_BASE_FIXTURE"
            tool_specs = request["tools"]
        native_tools = {tool.get("name") for tool in tool_specs}
        assert not native_tools.intersection(
            {
                "exec_command",
                "write_stdin",
                "view_image",
                "request_user_input",
                "apply_patch",
            }
        )
        platform = [
            part["text"]
            for item in request["input"]
            if item.get("role") == "developer"
            for part in item.get("content", [])
            if part.get("text") == prompt
        ]
        assert platform == [prompt]
        developer_parts = [
            part["text"]
            for item in request["input"]
            if item.get("role") == "developer"
            for part in item.get("content", [])
        ]
        if lite:
            assert developer_parts.pop(0) == "HARNESS_BASE_FIXTURE"
        native_count = 6 if lite else 3
        assert len(developer_parts) == native_count + (index >= 2)
        assert developer_parts[0] == prompt
        assert developer_parts[1].startswith("<skills_instructions>\n")
        assert developer_parts[1].endswith("\n</skills_instructions>")
        assert developer_parts[2].startswith("<permissions instructions>\n")
        assert developer_parts[2].endswith("</permissions instructions>")
        if lite:
            for part, tag in zip(
                developer_parts[3:6],
                ("collaboration_mode", "multi_agent_role", "multi_agent_mode"),
                strict=True,
            ):
                assert part.startswith(f"<{tag}>") and part.endswith(f"</{tag}>")
        if index >= 2:
            assert developer_parts[native_count] == (
                "<skills_instructions>\n## Orchestrator skills update\n"
                "No orchestrator skills are currently available.\n"
                "</skills_instructions>"
            )
        if lite:
            executors = [tool for tool in tool_specs if tool["name"] == "exec"]
            assert len(executors) == 1
            declaration = (
                "### `cheese_fixture`\nContract fixture\n\nexec tool declaration:\n"
                "```ts\ndeclare const tools: { "
                "cheese_fixture(args: { value: string; }): "
                "Promise<unknown>; };\n```"
            )
            assert executors[0]["description"].count(declaration) == 1
            for name in ("exec_command", "write_stdin", "view_image"):
                assert f"### `{name}`" not in executors[0]["description"]
        else:
            tools = [
                tool for tool in tool_specs if tool.get("name") == "cheese_fixture"
            ]
            assert len(tools) == 1
            assert tools[0]["parameters"] == {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            }
    tool_results = [
        item
        for item in requests[1]["input"]
        if item["type"]
        == (
            "custom_tool_call_output"
            if model == "gpt-6-astra"
            else "function_call_output"
        )
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
