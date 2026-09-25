"""App-server tool schemas, binary content and executor receipt identity."""

import json
import subprocess
import sys

import pytest

from app.domain.agent.harness.codex.bundle import build
from app.domain.agent.harness.codex.tools import RemoteTools


@pytest.mark.anyio
async def test_native_read_image_remains_an_image(monkeypatch):
    tools = RemoteTools({})
    tools.routes = {"Read": ("native", "Read")}
    monkeypatch.setattr(
        tools.client,
        "call",
        lambda *_: {
            "value": {
                "type": "image",
                "file": {"base64": "aW1hZ2U=", "type": "image/png"},
            }
        },
    )
    result = await tools(
        "item/tool/call",
        {
            "tool": "Read",
            "callId": "image-1",
            "arguments": {"file_path": "image.png"},
        },
    )
    assert result == {
        "success": True,
        "contentItems": [
            {"type": "inputImage", "imageUrl": "data:image/png;base64,aW1hZ2U="},
        ],
    }


@pytest.mark.anyio
async def test_schema_pages_and_multimodal_results_keep_their_content(monkeypatch):
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    calls = []

    def call(method, params):
        calls.append((method, params))
        if method == "mcp":
            if params["server"] == "native":
                return {
                    "tools": [
                        {"name": "Bash", "inputSchema": schema},
                        {"name": "Agent", "inputSchema": schema},
                    ]
                }
            if not params["params"]:
                return {"tools": [], "nextCursor": "second"}
            assert params["params"] == {"cursor": "second"}
            return {"tools": [{"name": "view", "inputSchema": schema}]}
        return {
            "value": {
                "content": [
                    {"type": "text", "text": "caption"},
                    {"type": "image", "mimeType": "image/png", "data": "aW1hZ2U="},
                ],
                "structuredContent": {"page": 2},
            }
        }

    tools = RemoteTools({})
    monkeypatch.setattr(tools.client, "call", call)
    schemas = await tools.discover(["docs"])
    names = [tool["name"] for tool in schemas]
    assert names[:2] == ["Bash", "mcp__docs__view"]
    assert all(tool["inputSchema"] == schema for tool in schemas[:2])
    result = await tools(
        "item/tool/call",
        {
            "tool": "mcp__docs__view",
            "callId": "call-17",
            "arguments": {"path": "中文"},
        },
    )
    assert calls[-1] == (
        "invoke",
        {
            "id": "call-17",
            "server": "docs",
            "tool": "view",
            "args": {"path": "中文"},
        },
    )
    assert result == {
        "success": True,
        "contentItems": [
            {"type": "inputText", "text": "caption"},
            {"type": "inputImage", "imageUrl": "data:image/png;base64,aW1hZ2U="},
            {"type": "inputText", "text": json.dumps({"page": 2})},
        ],
    }


@pytest.mark.anyio
async def test_platform_tools_are_listed_and_answered_by_the_backend(monkeypatch):
    """The platform's table arrives with the executor's tools, and a call to one
    goes to the backend: the executor is never asked for it or about it."""
    executor_calls = []

    def call(method, params):
        executor_calls.append(method)
        return {"tools": []}

    requests = []

    def platform_request(plan):
        requests.append(plan)
        return {"value": {"stdout": json.dumps({"data": {"hits": []}})}}

    monkeypatch.setenv("CHEESE_TOPIC", "room")
    monkeypatch.setenv("CHEESE_PROJECT", "project")
    tools = RemoteTools({})
    monkeypatch.setattr(tools.client, "call", call)
    monkeypatch.setattr(tools.client, "platform_request", platform_request)
    names = {tool["name"] for tool in await tools.discover([])}
    assert {"chat_send", "cheese_recall", "cheese_task"} <= names

    executor_calls.clear()
    result = await tools(
        "item/tool/call",
        {"tool": "cheese_recall", "callId": "c-1", "arguments": {"query": "技术栈"}},
    )
    assert result["success"] is True
    assert "没有找到相关记忆" in result["contentItems"][0]["text"]
    assert requests[0]["path"] == "/projects/project/memory/search"
    assert executor_calls == []


def test_runner_archive_launches_without_the_backend_environment(tmp_path):
    archive = tmp_path / "runner.pyz"
    archive.write_bytes(build())
    assert archive.read_bytes() == build()
    process = subprocess.run(
        [sys.executable, "-I", "-S", str(archive), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert "--state" in process.stdout


def test_the_runner_archive_carries_the_platform_tool_table(tmp_path):
    """The session host has no source tree: the table has to arrive inside the
    archive, and load from there."""
    archive = tmp_path / "runner.pyz"
    archive.write_bytes(build())
    probe = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from app.domain.agent.harness.codex.tools import platform_tools;"
        "print(sorted(platform_tools().PLATFORM_TOOLS.names())[:3])"
    )
    process = subprocess.run(
        [sys.executable, "-I", "-S", "-c", probe, str(archive)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert process.returncode == 0, process.stderr
    assert "chat_send" in process.stdout
