"""A Codex runner that can no longer read its session ends it.

The runner reads the agent's stdout in one background task. When that task
died (a line it cannot read, an event its handler fails on), nothing read the
session any more, yet the agent process went on: the runner kept answering
that the session was alive with its turn running, the backend kept reusing it
(``codex.host.configure`` reuses a runner whose ping says alive), and the turn
never ended.

The session must end instead, the way a session that exits by itself does: its
process is stopped, the runner goes, and the next ``configure`` starts a new
one. Why is printed to the runner's log.

The pinned Codex plays itself; the model is a stand-in that answers with one
message longer than the runner reads in a line, which is what ends the reading
here. Codex writes a finished message back on stdout whole.
"""

import asyncio
import json
import os
import signal
import subprocess
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.agent.harness.codex.bundle import build
from app.domain.agent.harness.codex.host import configure
from app.domain.agent.harness.driven.runner import socket_path
from tests.pinned_claude import codex_binary

pytestmark = pytest.mark.anyio

#: Past the 4 MiB the Codex runner reads a line up to.
TOO_LONG = "x" * (5 * 1024 * 1024)


async def test_a_session_its_runner_cannot_read_is_ended(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/execution":
                data = json.dumps({"tools": []}).encode()
                content_type = "application/json"
            else:
                item = {
                    "id": "message-1",
                    "type": "message",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {"type": "output_text", "text": TOO_LONG, "annotations": []}
                    ],
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
                        "type": "response.output_item.done",
                        "output_index": 0,
                        "item": item,
                    },
                    {"type": "response.completed", "response": response},
                ]
                data = "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()
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
    state = home / "runner"
    artifact = tmp_path / "runner.pyz"
    artifact.write_bytes(build())
    launch = {
        "state": str(state),
        "config": {
            "execution_target": {"kind": "device", "url": endpoint + "/execution"},
            "opening": {"system_prompt": "PROCESS_CONTRACT"},
            "binary": codex_binary(),
            "cwd": str(workspace),
        },
        "codex_config": (
            'model = "gpt-6-astra"\nmodel_provider = "fixture"\n'
            '[model_providers.fixture]\nname = "fixture"\n'
            f'base_url = "{endpoint}/v1"\nwire_api = "responses"\n'
            'env_key = "CHEESE_TOKEN"\n'
            "requires_openai_auth = false\n[analytics]\nenabled = false\n"
        ),
        "artifact": str(artifact),
        "env": {"CHEESE_TOKEN": "fixture", "NO_PROXY": "127.0.0.1"},
    }
    pid = (await asyncio.to_thread(configure, launch))["pid"]

    async def rpc(method, params=None):
        reader, writer = await asyncio.open_unix_connection(socket_path(state))
        try:
            writer.write(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            await writer.drain()
            return json.loads(await reader.readline())["result"]
        finally:
            writer.close()
            await writer.wait_closed()

    exited = asyncio.ensure_future(asyncio.to_thread(os.waitpid, pid, 0))
    try:
        await rpc(
            "send",
            {
                "input_id": "message-1",
                "text": "reply at length",
                "work_id": str(uuid.uuid4()),
            },
        )
        # Before the fix the runner stays, saying alive with the turn running,
        # for as long as anyone asks.
        await asyncio.wait_for(asyncio.shield(exited), 30)
    finally:
        if not exited.done():
            # A runner that stopped reading does not finish closing either: it
            # waits on a process whose output nobody takes.
            os.kill(pid, signal.SIGKILL)
            await exited
        server.shutdown()
        server.server_close()
        worker.join()
    log = (state / "runner.log").read_text()
    assert "can no longer be read" in log, log
