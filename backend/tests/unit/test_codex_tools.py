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
    assert [tool["name"] for tool in schemas] == ["Bash", "mcp__docs__view"]
    assert all(tool["inputSchema"] == schema for tool in schemas)
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
