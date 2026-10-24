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
        lambda *_, **__: {
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
    """Every page of the executor's tools is listed, and a picture a call
    answers with reaches the model as one."""
    schema = {"type": "object", "properties": {"path": {"type": "string"}}}
    calls = []

    def call(method, params, **_):
        calls.append((method, params))
        if method == "mcp":
            assert params["server"] == "native"
            if not params["params"]:
                return {
                    "tools": [
                        {"name": "Bash", "inputSchema": schema},
                        {"name": "Agent", "inputSchema": schema},
                    ],
                    "nextCursor": "second",
                }
            assert params["params"] == {"cursor": "second"}
            return {"tools": [{"name": "Glob", "inputSchema": schema}]}
        return {
            "value": {
                "type": "image",
                "file": {"type": "image/png", "base64": "aW1hZ2U="},
            }
        }

    tools = RemoteTools({"mcp_servers": ["docs"]})
    monkeypatch.setattr(tools.client, "call", call)
    schemas = await tools.discover()
    names = [tool["name"] for tool in schemas]
    assert names[:2] == ["Bash", "Glob"]
    assert not [name for name in names if name.startswith("mcp__")], (
        "Codex's own MCP client reaches the project's servers"
    )
    assert all(tool["inputSchema"] == schema for tool in schemas[:2])
    result = await tools(
        "item/tool/call",
        {"tool": "Glob", "callId": "call-17", "arguments": {"path": "中文"}},
    )
    assert calls[-1] == (
        "invoke",
        {"id": "call-17", "server": "native", "tool": "Glob", "args": {"path": "中文"}},
    )
    assert result == {
        "success": True,
        "contentItems": [
            {"type": "inputImage", "imageUrl": "data:image/png;base64,aW1hZ2U="},
        ],
    }


@pytest.mark.anyio
async def test_platform_tools_are_listed_and_answered_by_the_backend(monkeypatch):
    """The platform's table arrives with the executor's tools, and a call to one
    goes to the backend: the executor is never asked for it or about it."""
    executor_calls = []

    def call(method, params, **_):
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
    names = {tool["name"] for tool in await tools.discover()}
    assert {"chat_send", "cheese_notify", "cheese_task"} <= names

    executor_calls.clear()
    result = await tools(
        "item/tool/call",
        {
            "tool": "cheese_notify",
            "callId": "c-1",
            "arguments": {"title": "看一眼"},
        },
    )
    assert result["success"] is True
    assert requests[0]["path"] == "/projects/project/alerts"
    assert executor_calls == []
    # Codex's runner reports no session lifecycle, so it names no run: its
    # task-state reports carry none and always stand.
    assert "run" not in requests[0]


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


@pytest.mark.anyio
async def test_task_stop_stops_a_monitor_by_its_id_and_a_command_by_its_own(
    monkeypatch,
):
    """`TaskStop` is the one way to stop either: a monitor's id is the
    monitor's, and every other id is the executor's background command."""
    tools = RemoteTools({})
    tools.routes = {"TaskStop": ("native", "TaskStop")}
    stopped, sent = [], []

    class Monitors:
        async def stop(self, task):
            stopped.append(task)
            return f"Monitor {task} stopped"

    tools.monitors = Monitors()
    monkeypatch.setattr(
        tools.client,
        "call",
        lambda method, params, **_: sent.append(params) or {"value": "stopped"},
    )
    answer = await tools(
        "item/tool/call",
        {"tool": "TaskStop", "callId": "1", "arguments": {"task_id": "monitor-abc"}},
    )
    assert answer["success"] is True
    assert stopped == ["monitor-abc"] and sent == []

    await tools(
        "item/tool/call",
        {"tool": "TaskStop", "callId": "2", "arguments": {"task_id": "b7k2"}},
    )
    assert stopped == ["monitor-abc"]
    assert [params["args"] for params in sent] == [{"task_id": "b7k2"}]
