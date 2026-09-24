"""Exercise platform tool policy in the pinned interactive Claude process."""

import json
import os
import pty
import select
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.domain.agent.harness.claude_code.cli import disallowed_tools
from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION
from app.domain.agent.harness.claude_code.session_launch import _gates, hooks_settings


def _interactive_tools(tmp_path, remote_control=None):
    binary = shutil.which("claude")
    assert binary, "Install the pinned binary with scripts.test_harness_contracts"
    version = subprocess.check_output([binary, "--version"], text=True, timeout=10)
    assert version.startswith(CLAUDE_PINNED_VERSION + " "), version
    tools = set()
    received = threading.Event()

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if "/messages" not in self.path or "count_tokens" in self.path:
                data = b'{"input_tokens":1}'
                content_type = "application/json"
            else:
                if body.get("tools"):
                    tools.update(tool["name"] for tool in body["tools"])
                    received.set()
                events = [
                    {
                        "type": "message_start",
                        "message": {
                            "id": "msg_fixture",
                            "type": "message",
                            "role": "assistant",
                            "model": body["model"],
                            "content": [],
                            "stop_reason": None,
                            "usage": {"input_tokens": 1, "output_tokens": 0},
                        },
                    },
                    {
                        "type": "message_delta",
                        "delta": {"stop_reason": "end_turn"},
                        "usage": {"output_tokens": 1},
                    },
                    {"type": "message_stop"},
                ]
                data = "".join(
                    f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
                ).encode()
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    home, workspace = tmp_path / "home", tmp_path / "workspace"
    config = home / ".claude"
    config.mkdir(parents=True)
    workspace.mkdir()
    gates = json.loads(_gates(str(workspace)))
    gates["theme"] = "dark"
    (config / ".claude.json").write_text(json.dumps(gates))
    settings = {"skipDangerousModePermissionPrompt": True}
    arguments = []
    if remote_control is not None:
        # Exercise the RC permission policy without connecting to a live RC
        # service. Question transport itself has a separate contract.
        settings["permissions"] = hooks_settings(remote_control=remote_control)[
            "permissions"
        ]
        arguments = [
            "--disallowedTools",
            *disallowed_tools(remote_control=remote_control),
        ]
    (config / "settings.json").write_text(json.dumps(settings))
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [
            binary,
            "--model",
            "opus",
            "--dangerously-skip-permissions",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--setting-sources",
            "user",
            *arguments,
            "--",
            "Reply fixture only",
        ],
        cwd=workspace,
        env={
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(config),
            "TERM": "xterm-256color",
            "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        },
        stdin=slave,
        stdout=slave,
        stderr=slave,
        start_new_session=True,
    )
    os.close(slave)
    output = bytearray()
    approved = False
    try:
        deadline = time.monotonic() + 30
        while not received.is_set() and time.monotonic() < deadline:
            if select.select([master], [], [], 0.2)[0]:
                try:
                    output.extend(os.read(master, 65536))
                except OSError:
                    break
                if not approved and b"ANTHROPIC_API_KEY" in output:
                    # Approve this test's loopback-only fake key at first launch.
                    os.write(master, b"\x1b[A\r")
                    approved = True
        assert received.is_set(), output.decode(errors="replace")
        return tools
    finally:
        (tmp_path / "runner.log").write_bytes(output)
        (tmp_path / "tool-inventory.json").write_text(json.dumps(sorted(tools)))
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        os.close(master)
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_pinned_interactive_binary_has_native_reminders(tmp_path):
    assert {"CronCreate", "CronDelete", "CronList", "ScheduleWakeup"} <= (
        _interactive_tools(tmp_path)
    )


@pytest.mark.parametrize("remote_control", [False, True])
def test_platform_owns_tasks_and_reminders_in_both_question_modes(
    tmp_path, remote_control
):
    tools = _interactive_tools(tmp_path, remote_control)
    assert not tools & {
        "TodoWrite",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "CronCreate",
        "CronDelete",
        "CronList",
        "ScheduleWakeup",
    }
    assert {"Agent", "SendMessage", "TaskStop", "Bash", "Read"} <= tools
    assert ("AskUserQuestion" in tools) is remote_control
