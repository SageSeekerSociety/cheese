"""Pinned native child retasking and targeted stop against a scripted provider."""

import json
import os
import re
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app.domain.agent.harness.claude_code.device_launch import CLAUDE_PINNED_VERSION


def _events(model, content):
    events = [
        {
            "type": "message_start",
            "message": {
                "id": "msg_fixture",
                "type": "message",
                "role": "assistant",
                "model": model,
                "content": [],
                "stop_reason": None,
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        }
    ]
    for index, original in enumerate(content):
        block = dict(original)
        if block["type"] == "tool_use":
            delta = {
                "type": "input_json_delta",
                "partial_json": json.dumps(block.pop("input")),
            }
            block["input"] = {}
        else:
            delta = {"type": "text_delta", "text": block.pop("text")}
            block["text"] = ""
        events.extend(
            [
                {"type": "content_block_start", "index": index, "content_block": block},
                {"type": "content_block_delta", "index": index, "delta": delta},
                {"type": "content_block_stop", "index": index},
            ]
        )
    events.extend(
        [
            {
                "type": "message_delta",
                "delta": {
                    "stop_reason": "tool_use"
                    if any(b["type"] == "tool_use" for b in content)
                    else "end_turn"
                },
                "usage": {"output_tokens": 1},
            },
            {"type": "message_stop"},
        ]
    )
    return "".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events
    ).encode()


def _tool(name, identifier, **arguments):
    return {"type": "tool_use", "id": identifier, "name": name, "input": arguments}


def _wait(predicate, description):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError(description)


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def test_native_send_message_retasks_and_task_stop_only_stops_target(tmp_path):
    version = subprocess.check_output(["claude", "--version"], text=True, timeout=10)
    assert version.startswith(CLAUDE_PINNED_VERSION + " "), version
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)
    requests, errors, observations = [], [], []
    ids = {}
    parent_stage = 0
    child_turns = {"a": 0, "b": 0}
    b_done = threading.Event()
    retasked = threading.Event()

    def running(name):
        return (workspace / f"{name}.started").exists()

    def bash_wait(name):
        return _tool(
            "Bash",
            f"toolu_wait_{name}",
            command=(
                f"echo $$ > {name}.pid; touch {name}.started; "
                f"for n in $(seq 1 300); do [ -f release-{name} ] && break; "
                f"sleep 0.1; done; touch {name}.finished"
            ),
            description=f"Wait for fixture release {name}",
            timeout=40000,
        )

    def answer(body, headers):
        nonlocal parent_stage
        if errors:
            return [{"type": "text", "text": "Fixture ended after a failed assertion"}]
        messages = json.dumps(body.get("messages", []))
        is_child = headers.get("x-claude-code-request-class") == "subagent"
        if is_child:
            name = "a" if "CHILD_A" in messages else "b"
            turn = child_turns[name]
            child_turns[name] += 1
            if turn == 0:
                return [bash_wait(name)]
            if name == "a":
                assert "RETASK_A_ONLY" in messages, messages
                assert "RETASK_B" not in messages
                retasked.set()
                observations.append(
                    "A received the new instruction in a real native model request"
                )
                return [bash_wait("a2")]
            assert "RETASK_A_ONLY" not in messages, messages
            b_done.set()
            observations.append("B resumed after A was stopped")
            return [{"type": "text", "text": "B completed after its own release"}]
        stage = parent_stage
        parent_stage += 1
        if stage == 0:
            assert {"Agent", "SendMessage", "TaskStop"} <= {
                t["name"] for t in body["tools"]
            }
            return [
                _tool(
                    "Agent",
                    f"toolu_child_{name}",
                    description=f"Run fixture child {name}",
                    prompt=f"CHILD_{name.upper()} run the controlled wait.",
                    subagent_type="general-purpose",
                    run_in_background=True,
                )
                for name in ("a", "b")
            ]
        if stage == 1:
            for message in body["messages"]:
                for block in message.get("content", []):
                    if isinstance(block, dict) and block.get(
                        "tool_use_id", ""
                    ).startswith("toolu_child_"):
                        found = re.search(
                            r"agentId: ([a-zA-Z0-9_-]+)", json.dumps(block)
                        )
                        assert found, block
                        ids[block["tool_use_id"][-1]] = found.group(1)
            assert set(ids) == {"a", "b"}
            _wait(
                lambda: running("a") and running("b"),
                "both native children execute Bash",
            )
            return [
                _tool(
                    "SendMessage",
                    "toolu_retask",
                    to=ids["a"],
                    message=(
                        "RETASK_A_ONLY: change the task and wait at the second barrier."
                    ),
                    summary="Change only the first child task",
                )
            ]
        if stage == 2:
            (workspace / "release-a").touch()
            _wait(
                lambda: retasked.is_set() and running("a2"),
                "retasked A starts a second real Bash wait",
            )
            return [_tool("TaskStop", "toolu_stop_a", task_id=ids["a"])]
        if stage == 3:
            stopped_pid = int((workspace / "a2.pid").read_text())
            _wait(
                lambda: not _alive(stopped_pid), "TaskStop terminates target child tool"
            )
            assert not (workspace / "a2.finished").exists()
            sibling_pid = int((workspace / "b.pid").read_text())
            assert _alive(sibling_pid)
            assert not (workspace / "b.finished").exists()
            observations.append(
                "TaskStop killed A's Bash process while B's Bash process stayed alive"
            )
            (workspace / "release-b").touch()
            return [
                _tool(
                    "Bash",
                    "toolu_parent_alive",
                    command="printf alive > parent-alive.txt",
                    description="Prove the parent still executes tools",
                )
            ]
        _wait(b_done.is_set, "sibling completes after release")
        assert (workspace / "parent-alive.txt").read_text() == "alive"
        return [{"type": "text", "text": "Native child control fixture complete"}]

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"headers": dict(self.headers), "body": body})
            if "/messages" not in self.path or "count_tokens" in self.path:
                payload, content_type = b'{"input_tokens":1}', "application/json"
            else:
                try:
                    content = answer(body, self.headers)
                except Exception as exc:
                    errors.append(repr(exc))
                    # A failed assertion must not strand a native background tool.
                    for name in ("a", "a2", "b"):
                        (workspace / f"release-{name}").touch()
                    content = [{"type": "text", "text": "Fixture failed"}]
                payload = _events(body["model"], content)
                content_type = "text/event-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / "home"
    home.mkdir()
    try:
        result = subprocess.run(
            [
                "claude",
                "--print",
                "--model",
                "opus",
                "--output-format",
                "json",
                "--dangerously-skip-permissions",
                "--strict-mcp-config",
                "--setting-sources",
                "user",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--",
                "Run the native child control fixture.",
            ],
            cwd=workspace,
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "ANTHROPIC_API_KEY": "fixture-not-a-real-key",
                "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{server.server_port}",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                "CLAUDE_CODE_GATEWAY_HINT_HEADERS": "1",
            },
            capture_output=True,
            timeout=75,
        )
        (tmp_path / "runner.log").write_bytes(result.stdout + b"\n" + result.stderr)
        assert result.returncode == 0, result.stderr.decode()
        assert not errors, errors
        summary = json.loads(result.stdout)
        assert summary["subagent_stats"]["spawned"] == 2
        assert summary["subagent_stats"]["completed"] == 1
        assert summary["subagent_stats"]["killed"]["parent"] == 1
        assert summary["permission_denials"] == []
        assert retasked.is_set() and b_done.is_set()
        assert len(observations) == 3, observations
        assert (workspace / "parent-alive.txt").read_text() == "alive"
    finally:
        for name in ("a", "a2", "b"):
            (workspace / f"release-{name}").touch()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
        (tmp_path / "provider-requests.json").write_text(json.dumps(requests, indent=2))
        (tmp_path / "observations.json").write_text(
            json.dumps(
                {
                    "version": version.strip(),
                    "observations": observations,
                    "errors": errors,
                },
                indent=2,
            )
        )
