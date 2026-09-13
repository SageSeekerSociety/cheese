"""Exercise the production interactive launcher against a scripted model endpoint."""

import fcntl
import json
import os
import pty
import select
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.agent.harness.claude_code.remote_execution import client, runtime
from tests.support.harness_prompts import system_prompt


@pytest.mark.skipif(shutil.which("claude") is None, reason="Claude binary required")
def test_interactive_remote_tool_hooks_history_and_resume(tmp_path):
    requests = []
    executor = tmp_path / "executor"
    executor.mkdir()
    state = tmp_path / "executor-state"
    hooks = tmp_path / "hooks.jsonl"
    recorder = tmp_path / "record_hook.py"
    recorder.write_text(
        "import sys\n"
        "with open(sys.argv[1], 'a') as output:\n"
        "    output.write(sys.stdin.read().strip() + '\\n')\n"
    )
    hook_command = shlex.join([sys.executable, str(recorder), str(hooks)])
    prompt = system_prompt()
    command = "printf INTERACTIVE_TOOL_OK > result.txt; cat result.txt"

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/messages" not in self.path or "count_tokens" in self.path:
                data = b'{"input_tokens":10}'
                content_type = "application/json"
            else:
                requests.append(request)
                first = len(requests) == 1
                block = (
                    {
                        "type": "tool_use",
                        "id": "tool_interactive",
                        "name": "Bash",
                        "input": {},
                    }
                    if first
                    else {"type": "text", "text": ""}
                )
                delta = (
                    {
                        "type": "input_json_delta",
                        "partial_json": json.dumps({"command": command}),
                    }
                    if first
                    else {
                        "type": "text_delta",
                        "text": f"INTERACTIVE_REPLY_{len(requests)}",
                    }
                )
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": f"msg_{len(requests)}",
                            "type": "message",
                            "role": "assistant",
                            "model": request["model"],
                            "content": [],
                            "stop_reason": None,
                            "stop_sequence": None,
                            "usage": {"input_tokens": 10, "output_tokens": 0},
                        },
                    },
                    {"type": "content_block_start", "index": 0, "content_block": block},
                    {"type": "content_block_delta", "index": 0, "delta": delta},
                    {"type": "content_block_stop", "index": 0},
                    {
                        "type": "message_delta",
                        "delta": {
                            "stop_reason": "tool_use" if first else "end_turn",
                            "stop_sequence": None,
                        },
                        "usage": {"output_tokens": 10},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"
                    for event in events
                ).encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=provider.serve_forever, daemon=True)
    worker.start()
    process = None
    master = None
    transcript = tmp_path / "terminal.log"

    def stop():
        nonlocal process, master
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=10)
        if master is not None:
            os.close(master)
            master = None

    def wait_for(predicate, description):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if predicate():
                return
            assert process.poll() is None, (
                f"Claude exited while waiting for {description}"
            )
            if select.select([master], [], [], 0.05)[0]:
                with transcript.open("ab") as output:
                    output.write(os.read(master, 65536))
        pytest.fail(
            f"Timed out waiting for {description}; see terminal.log and debug logs"
        )

    try:
        subprocess.run(
            [sys.executable, runtime.__file__, "start", "--state", str(state)],
            input=json.dumps(
                {"workspace": str(executor), "claude": shutil.which("claude")}
            ),
            text=True,
            capture_output=True,
            check=True,
            timeout=30,
        )
        launch = client.prepare(
            tmp_path / "central",
            {"command": [sys.executable, runtime.__file__], "state": str(state)},
            extra_args=[
                "--dangerously-skip-permissions",
                "--model",
                "claude-sonnet-4-6",
                "--append-system-prompt",
                prompt,
            ],
            base_settings={
                "hooks": {
                    name: [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": hook_command}],
                        }
                    ]
                    for name in ("PreToolUse", "PostToolUse")
                }
            },
        )

        def start(resume=None):
            nonlocal process, master
            debug = tmp_path / ("resume-debug.log" if resume else "start-debug.log")
            master, slave = pty.openpty()
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 130, 0, 0))
            args = [*launch["command"], "--debug-file", str(debug)]
            if resume:
                args.extend(["--resume", resume])
            process = subprocess.Popen(
                args,
                cwd=launch["cwd"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                start_new_session=True,
                env={
                    "PATH": os.environ["PATH"],
                    "SHELL": "/bin/bash",
                    "TERM": "xterm-256color",
                    **launch["env"],
                    "ANTHROPIC_AUTH_TOKEN": "fixture-not-a-real-key",
                    "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{provider.server_port}",
                    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                },
            )
            os.close(slave)
            wait_for(
                lambda: (
                    debug.exists() and "[REPL:mount] REPL mounted" in debug.read_text()
                ),
                "interactive mount",
            )

        def send(text, reply):
            os.write(master, text.encode())
            # Interactive paste handling must finish before Enter submits the buffer.
            time.sleep(0.7)
            os.write(master, b"\r")
            wait_for(
                lambda: (
                    transcript.exists() and reply.encode() in transcript.read_bytes()
                ),
                reply,
            )

        start()
        send("CI_FIRST_TURN: run the tool", "INTERACTIVE_REPLY_2")
        assert (executor / "result.txt").read_text() == "INTERACTIVE_TOOL_OK"
        assert not (tmp_path / "central/workspace/result.txt").exists()
        send("CI_SECOND_TURN: retain the previous result", "INTERACTIVE_REPLY_3")
        receipts = [json.loads(line) for line in hooks.read_text().splitlines()]
        assert [(item["hook_event_name"], item["tool_name"]) for item in receipts] == [
            ("PreToolUse", "Bash"),
            ("PostToolUse", "Bash"),
        ]
        session_id = receipts[0]["session_id"]
        stop()
        start(session_id)
        send("CI_RESUMED_TURN: retain both prior turns", "INTERACTIVE_REPLY_4")
        assert len(requests) == 4
        for request in requests:
            assert prompt in "\n".join(
                block.get("text", "") for block in request["system"]
            )
            assert "Bash" in {tool["name"] for tool in request["tools"]}
        history = json.dumps(requests[-1]["messages"])
        for marker in (
            "CI_FIRST_TURN",
            "CI_SECOND_TURN",
            "CI_RESUMED_TURN",
            "INTERACTIVE_TOOL_OK",
            "tool_interactive",
        ):
            assert marker in history
    finally:
        stop()
        subprocess.run(
            [sys.executable, runtime.__file__, "stop", "--state", str(state)],
            capture_output=True,
            timeout=30,
        )
        provider.shutdown()
        provider.server_close()
        worker.join()
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))
