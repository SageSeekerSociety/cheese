"""The deployed archive owns a real app-server without importing the backend."""

import asyncio
import base64
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from PIL import Image

from app.domain.agent.harness.codex.bundle import build
from app.domain.agent.harness.codex.runner import socket_path


@pytest.mark.anyio
@pytest.mark.skipif(shutil.which("codex") is None, reason="Codex binary required")
@pytest.mark.parametrize("attempt_host_write", [False, True])
async def test_standalone_owner_survives_client_disconnect(
    tmp_path, attempt_host_write
):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/execution":
                assert self.headers["X-Cheese-Token"] == "fixture"
                assert body["method"] == "mcp"
                data = json.dumps({"tools": []}).encode()
                content_type = "application/json"
            else:
                requests.append(body)
                item = {
                    "id": "message-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "independent process reply",
                            "annotations": [],
                        }
                    ],
                }
                if attempt_host_write and len(requests) == 1:
                    patch = (
                        "*** Begin Patch\n*** Add File: host-write.txt\n"
                        "+unexpected\n*** End Patch"
                    )
                    item = {
                        "id": "write-attempt",
                        "type": "custom_tool_call",
                        "call_id": "write-attempt",
                        "name": "exec",
                        "namespace": "functions",
                        "input": f"text(await tools.apply_patch({json.dumps(patch)}));",
                        "status": "completed",
                    }
                response = {
                    "id": "response-1",
                    "object": "response",
                    "output": [item],
                    "status": "completed",
                }
                events = [
                    {
                        "type": "response.created",
                        "response": {**response, "status": "in_progress", "output": []},
                    },
                    {
                        "type": "response.output_item.added",
                        "output_index": 0,
                        "item": item,
                    },
                    {
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": item,
                    },
                    {"type": "response.completed", "response": response},
                ]
                data = "".join(
                    f"data: {json.dumps(event)}\n\n" for event in events
                ).encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    endpoint = f"http://127.0.0.1:{server.server_port}"
    (home / "config.toml").write_text(
        'model = "gpt-6-astra"\nmodel_provider = "fixture"\n'
        '[model_providers.fixture]\nname = "fixture"\n'
        f'base_url = "{endpoint}/v1"\nwire_api = "responses"\n'
        "requires_openai_auth = false\n[analytics]\nenabled = false\n"
    )
    config = tmp_path / "runner.json"
    config.write_text(
        json.dumps(
            {
                "execution_target": {"kind": "device", "url": endpoint + "/execution"},
                "opening": {"system_prompt": "PROCESS_CONTRACT"},
                "mcp_servers": [],
                "binary": shutil.which("codex"),
                "cwd": str(workspace),
            }
        )
    )
    archive = tmp_path / "runner.pyz"
    archive.write_bytes(build())
    state = home / "runner"
    log = (tmp_path / "runner.log").open("w")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        str(archive),
        "--state",
        str(state),
        "--config",
        str(config),
        cwd=workspace,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "CODEX_HOME": str(home),
            "CHEESE_TOKEN": "fixture",
            "NO_PROXY": "127.0.0.1",
        },
        stdout=log,
        stderr=log,
    )

    async def rpc(method, params=None, *, disconnect=False):
        reader, writer = await asyncio.open_unix_connection(socket_path(state))
        try:
            writer.write(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            await writer.drain()
            if disconnect:
                return None
            result = json.loads(await reader.readline())
            assert "error" not in result, result
            return result["result"]
        finally:
            writer.close()
            await writer.wait_closed()

    try:
        async with asyncio.timeout(30):
            while not os.path.exists(socket_path(state)):
                assert process.returncode is None, (tmp_path / "runner.log").read_text()
                await asyncio.sleep(0.01)
            assert (await rpc("ping"))["pid"] == process.pid
            png = io.BytesIO()
            Image.new("RGB", (32, 32), "white").save(png, format="PNG")
            picture = (
                "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()
            )
            request = {
                "input_id": "message-1",
                "text": "reply once",
                "images": [picture],
            }
            await rpc("send", request, disconnect=True)
            accepted = await rpc("send", request)
            while True:
                records = (await rpc("events"))["events"]
                if any(row["record"]["method"] == "turn/completed" for row in records):
                    break
                await asyncio.sleep(0.01)
            assert await rpc("send", request) == accepted
            assert len(requests) == (2 if attempt_host_write else 1)
            assert not (workspace / "host-write.txt").exists()
            if attempt_host_write:
                outputs = [
                    item["output"]
                    for item in requests[1]["input"]
                    if item["type"] == "custom_tool_call_output"
                ]
                assert "tools.apply_patch is not a function" in json.dumps(outputs)
            user = [item for item in requests[0]["input"] if item.get("role") == "user"]
            assert user[-1]["content"][0] == {
                "type": "input_text",
                "text": "reply once",
            }
            pictures = [
                part for part in user[-1]["content"] if part["type"] == "input_image"
            ]
            assert len(pictures) == 1
            assert pictures[0]["image_url"].startswith("data:image/")
            decoded = Image.open(
                io.BytesIO(base64.b64decode(pictures[0]["image_url"].split(",", 1)[1]))
            )
            assert decoded.size == (32, 32)
            assert decoded.convert("RGB").getpixel((0, 0)) == (255, 255, 255)
            assert any(
                row["record"].get("params", {}).get("item", {}).get("text")
                == "independent process reply"
                for row in records
            )
    finally:
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))
        if process.returncode is None:
            process.terminate()
        await asyncio.wait_for(process.wait(), 10)
        log.close()
        server.shutdown()
        server.server_close()
        worker.join()
    assert process.returncode == 0, (tmp_path / "runner.log").read_text()
