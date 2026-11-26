#!/usr/bin/env python3
"""Run one pi build against this repo's pi extension, offline, and say what happened.

Manual, and deliberately not in CI: it downloads a vendor build (or takes one
already unpacked), and what it answers is a question about a *build*, asked when
someone bumps `harness/pi/launch.py`'s `VERSION` — the same question
`test_harness_contracts.py` asks of the Claude Code and Codex pins.

What it stands up, all on one machine and with no network:

* a stub model — an OpenAI-compatible **streaming** endpoint that plays a fixed
  three-step script (call `read`, call a platform tool, then answer);
* a stub runner — the unix socket the extension asks for files, hooks, context
  and the platform CLI, which records every request and answers placeholders;
* the real pi, launched with the argv `harness/pi/launch.py` builds.

It then prints the runner methods the extension called, the tool calls that
ran, and — the reason this exists — **the system prompt the model was actually
sent**, read back off the stub model's request. A pi that renamed the line the
extension rewrites shows up there and nowhere else, because that rewrite fails
silently.

    python3 backend/scripts/pi_build_smoke.py --pi <dir with the pi binary> \\
        --tag 1.0.0 --out /tmp/pi-smoke
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import shutil
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = pathlib.Path(__file__).resolve().parent
EXTENSION = HERE.parent / "app/domain/agent/harness/pi/platform.ts"

TOOL = {
    "name": "cheese_demo",
    "description": "A platform tool.",
    "inputSchema": {"type": "object", "properties": {}},
}


def completion_chunks(step: int) -> list[dict]:
    """The stub's three-step script, as OpenAI-compatible SSE chunks."""
    head = {"id": f"c{step}", "object": "chat.completion.chunk", "created": 0}
    if step == 1:
        call = [
            {
                "index": 0,
                "id": "call_r1",
                "type": "function",
                "function": {
                    "name": "read",
                    "arguments": json.dumps({"path": "hello.txt"}),
                },
            }
        ]
        finish = "tool_calls"
    elif step == 2:
        call = [
            {
                "index": 0,
                "id": "call_t1",
                "type": "function",
                "function": {"name": "cheese_demo", "arguments": "{}"},
            }
        ]
        finish = "tool_calls"
    else:
        call, finish = None, "stop"
    delta = {"tool_calls": call} if call else {"content": "SMOKE-DONE"}
    return [
        {
            **head,
            "choices": [
                {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
            ],
        },
        {**head, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {
            **head,
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        },
    ]


class StubRunner:
    """The session runner's socket, as the extension sees it."""

    def __init__(self, path: pathlib.Path, context: str) -> None:
        self.path = path
        self.context = context
        self.requests: list[dict] = []

    def answer(self, method: str, params: dict) -> dict:
        if method == "cli":
            return {"status": 0, "stdout": "STUB-CLI-OK", "stderr": ""}
        if method == "hooks":
            # Echo the input back: an empty one would strip the call's arguments.
            return {"input": params.get("input") or {}}
        if method == "context":
            return {"context": self.context}
        if method == "files" and params.get("operation") == "read":
            body = base64.b64encode(b"hello from the machine\n").decode()
            return {"data": body}
        return {}

    def serve(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        server.listen(32)
        while True:
            connection, _ = server.accept()
            with connection:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = connection.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                try:
                    request = json.loads(data.decode())
                except ValueError:
                    continue
                self.requests.append(request)
                answer = self.answer(
                    request.get("method", ""), request.get("params") or {}
                )
                connection.sendall(json.dumps({"result": answer}).encode() + b"\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pi", required=True, help="directory holding the pi binary")
    parser.add_argument("--out", required=True, help="where to write what was seen")
    parser.add_argument("--tag", default="pi", help="name for this run's files")
    parser.add_argument("--extension", default=str(EXTENSION))
    parser.add_argument(
        "--context", default="", help="what the runner answers for context"
    )
    parser.add_argument("--workspace", default="", help="the room machine's checkout")
    parser.add_argument("--hands", default="true", choices=["true", "false"])
    parser.add_argument("--timeout-s", type=float, default=120.0)
    options = parser.parse_args()

    pi = pathlib.Path(options.pi) / "pi"
    out = pathlib.Path(options.out)
    out.mkdir(parents=True, exist_ok=True)
    work = out / f"work-{options.tag}"
    shutil.rmtree(work, ignore_errors=True)
    for name in ("state", "home", "agent", "workspace", "sessions", "ext"):
        (work / name).mkdir(parents=True)

    runner = StubRunner(work / "sock", options.context)
    threading.Thread(target=runner.serve, daemon=True).start()

    sent: list[dict] = []

    class Model(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("content-length") or 0)
            sent.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.send_header("content-type", "text/event-stream")
            self.end_headers()
            for chunk in completion_chunks(len(sent)):
                self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

        def log_message(self, *args: object) -> None:
            pass

    model = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    port = model.server_address[1]
    threading.Thread(target=model.serve_forever, daemon=True).start()

    shutil.copy(pathlib.Path(options.extension), work / "ext" / "index.ts")
    (work / "ext" / "platform.json").write_text(
        json.dumps(
            {
                "socket": str(work / "sock"),
                "state": str(work / "state"),
                "hands": options.hands == "true",
                "workspace": options.workspace or str(work / "workspace"),
                "jobs": "",
                "tools": [TOOL],
                "unavailable": "",
                "mcp": [],
                "notice": "",
                "subagents": False,
            }
        )
    )
    (work / "agent" / "models.json").write_text(
        json.dumps(
            {
                "providers": {
                    "cheese": {
                        "baseUrl": f"http://127.0.0.1:{port}/v1",
                        "api": "openai-completions",
                        "apiKey": "stub",
                        "models": [{"id": "stub"}],
                    }
                }
            }
        )
    )

    command = [
        str(pi),
        "--mode",
        "rpc",
        "--session-id",
        "smoke",
        "--session-dir",
        str(work / "sessions"),
        "--provider",
        "cheese",
        "--model",
        "cheese/stub",
        "--no-context-files",
        "--no-skills",
        "--no-extensions",
        "--no-prompt-templates",
        "--no-themes",
        "--offline",
        "--system-prompt",
        "You are the smoke test agent.",
        "--tools",
        f"read,{TOOL['name']}",
        "--extension",
        str(work / "ext" / "index.ts"),
    ]
    environment = {
        **os.environ,
        "HOME": str(work / "home"),
        "PI_CODING_AGENT_DIR": str(work / "agent"),
        "PI_OFFLINE": "1",
        "PI_TELEMETRY": "0",
        "CHEESE_PI_EXTENSION": str(work / "ext"),
    }
    with (out / f"{options.tag}-pi.log").open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=str(work / "workspace"),
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=log,
        )
        assert process.stdin is not None and process.stdout is not None
        # Held in locals: pyright cannot narrow the process's own attributes
        # from inside a closure.
        incoming, outgoing = process.stdout, process.stdin
        events: list[dict] = []

        def read_stream() -> None:
            for raw in incoming:
                log.write(raw)
                log.flush()
                try:
                    events.append(json.loads(raw.decode("utf-8", "replace")))
                except ValueError:
                    pass

        threading.Thread(target=read_stream, daemon=True).start()
        time.sleep(3)

        def send(command_object: dict) -> None:
            try:
                outgoing.write(json.dumps(command_object).encode() + b"\n")
                outgoing.flush()
            except (BrokenPipeError, ValueError):
                pass

        send({"id": "1", "type": "prompt", "message": "run the demo"})
        deadline = time.monotonic() + options.timeout_s
        asked = False
        while time.monotonic() < deadline:
            if not asked and any(e.get("type") == "agent_end" for e in events):
                send({"id": "9", "type": "get_entries"})
                asked = True
                time.sleep(3)
                break
            time.sleep(0.5)
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()

    entries = next(
        (
            e.get("data", {}).get("entries")
            for e in events
            if e.get("command") == "get_entries"
        ),
        None,
    )
    report = {
        "version": subprocess.run(
            [str(pi), "--version"], capture_output=True, text=True, check=False
        ).stdout.strip(),
        "runner_calls": [r.get("method") for r in runner.requests],
        "tool_calls": [
            e.get("toolCallId")
            for e in events
            if e.get("type") == "tool_execution_start"
        ],
        "entry_types": sorted({e.get("type") for e in entries or []}),
        "model_calls": len(sent),
        "system_prompt": sent[0]["messages"][0]["content"] if sent else None,
    }
    (out / f"{options.tag}-report.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False)
    )
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
