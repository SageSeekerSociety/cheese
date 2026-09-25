"""The runner as the backend reaches it: over a socket, across a reconnection.

Driven against a real subprocess speaking pi's protocol rather than a fake
object, because what is being tested is the process, its pipes and its framing.
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness import Opening
from app.domain.agent.harness.pi.runner import Runner, socket_path

FAKE = Path(__file__).resolve().parents[1] / "support/fake_pi.py"
FIXTURE = Path(__file__).parent / "fixtures/pi-entries.json"


async def call(state: Path, method: str, params: dict | None = None) -> dict:
    reader, writer = await asyncio.open_unix_connection(socket_path(state))
    try:
        writer.write(
            json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
        )
        await writer.drain()
        answer = json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()
    if "error" in answer:
        raise RuntimeError(answer["error"])
    return answer["result"]


def shim(tmp_path) -> str:
    """A binary that takes pi's flags where pi takes them.

    The runner builds `<binary> --mode rpc --session-id ... <args>`, so the
    stand-in has to accept those in that position rather than after a script
    path — which is also the shape a real install has.
    """
    path = tmp_path / "pi"
    path.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {FIXTURE} "$@"\n')
    path.chmod(0o700)
    return str(path)


async def running(tmp_path, resume=None):
    runner = Runner(tmp_path / "state")
    session_id = await runner.start(
        Opening("system prompt", resume, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=["--no-context-files"],
    )
    return runner, session_id


@pytest.mark.anyio
async def test_the_platform_names_the_session_and_can_ask_for_it_again(tmp_path):
    runner, session_id = await running(tmp_path)
    try:
        assert uuid.UUID(session_id)
        assert (await call(runner.state, "ping"))["session_id"] == session_id
    finally:
        await runner.close()

    # The same directory resumes the same session, and refuses a different one.
    again, resumed = await running(tmp_path, resume=session_id)
    try:
        assert resumed == session_id
    finally:
        await again.close()
    with pytest.raises(ValueError):
        await running(tmp_path, resume=str(uuid.uuid4()))


@pytest.mark.anyio
async def test_an_input_reaches_the_model_once_however_often_it_is_resent(tmp_path):
    runner, _ = await running(tmp_path)
    try:
        identifier = str(uuid.uuid4())
        work = str(uuid.uuid4())
        payload = {"input_id": identifier, "text": "改一下 greet", "work_id": work}
        first = await call(runner.state, "send", payload)
        # A backend that never saw the answer resends the same id.
        assert await call(runner.state, "send", payload) == first

        entries = (await call(runner.state, "entries"))["entries"]
        assert len(entries) == len(json.loads(FIXTURE.read_text())["entries"])
        assert {entry["cheese"]["work_id"] for entry in entries} == {work}
        assert {entry["cheese"]["harness"] for entry in entries} == {"pi"}

        # One prompt reached pi, not two.
        stats = await runner.client.request("get_session_stats")
        assert stats["prompts"] == 1
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_reusing_an_input_id_for_different_text_is_refused(tmp_path):
    runner, _ = await running(tmp_path)
    try:
        identifier = str(uuid.uuid4())
        await call(runner.state, "send", {"input_id": identifier, "text": "一"})
        with pytest.raises(RuntimeError, match="different text"):
            await call(runner.state, "send", {"input_id": identifier, "text": "二"})
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_reader_with_a_cursor_is_given_only_what_it_has_not_seen(tmp_path):
    runner, _ = await running(tmp_path)
    try:
        await call(
            runner.state,
            "send",
            {
                "input_id": str(uuid.uuid4()),
                "text": "开始",
                "work_id": str(uuid.uuid4()),
            },
        )
        everything = (await call(runner.state, "entries"))["entries"]
        rest = (await call(runner.state, "entries", {"since": everything[2]["id"]}))[
            "entries"
        ]
        assert [entry["id"] for entry in rest] == [
            entry["id"] for entry in everything[3:]
        ]
        # A cursor naming an entry this runner never held replays rather than
        # skips; the ids make that harmless.
        assert (await call(runner.state, "entries", {"since": "gone"}))[
            "entries"
        ] == everything
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_steering_is_not_a_second_turn_and_abort_stops_the_work(tmp_path):
    runner, _ = await running(tmp_path)
    try:
        await call(
            runner.state,
            "send",
            {
                "input_id": str(uuid.uuid4()),
                "text": "开始",
                "work_id": str(uuid.uuid4()),
            },
        )
        await call(
            runner.state, "steer", {"input_id": str(uuid.uuid4()), "text": "换个名字"}
        )
        assert (await call(runner.state, "abort"))["aborted"] is True
        assert (await call(runner.state, "ping"))["working"] is False
    finally:
        await runner.close()


# --- the platform's tools, over the same socket ------------------------------
#
# pi has no MCP, so a room's platform tools reach it as extension tools whose
# calls come back here. The catalog is read off the platform file installed on
# the machine: its tool table, which runs here against the backend, and its
# argparse tree, whose commands run as the CLI — a command exists exactly when
# the CLI has it, and takes exactly what the command takes.

CLI = Path(__file__).resolve().parents[2] / "sandbox/cheese"

# Shape, not content: what the runner does with these files is write them
# where pi and the extension will look.
EXTENSION = {
    "index.ts": "export default function () {}\n",
    "background.py": "# holds one command\n",
}

ECHOING_CLI = '''#!/usr/bin/env python3
"""A CLI shaped like the platform's: a parser to publish, and argv to report."""
import argparse, json, os, sys


class _NoTools:
    def schemas(self):
        return []

    def __contains__(self, name):
        return False


PLATFORM_TOOLS = _NoTools()


def build_parser():
    p = argparse.ArgumentParser(prog="cheese")
    sub = p.add_subparsers(dest="cmd", required=True)
    sync = sub.add_parser("sync", description="同步任务")
    sync.add_argument("--task")
    sync.add_argument("note", nargs="?")
    work = sub.add_parser("worktree", description="准备目录")
    work.add_argument("task_id")
    return p


if __name__ == "__main__":
    build_parser().parse_args(sys.argv[1:])
    json.dump({"argv": sys.argv[1:], "cwd": os.getcwd()}, sys.stdout)
'''


def installed_cli(tmp_path, monkeypatch, program=None) -> Path:
    """Put a `cheese` on PATH the way a launched machine has one."""
    bindir = tmp_path / "cli-bin"
    bindir.mkdir(exist_ok=True)
    target = bindir / "cheese"
    target.write_text(CLI.read_text() if program is None else program)
    target.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    return target


async def with_tools(tmp_path, monkeypatch, program=None):
    installed_cli(tmp_path, monkeypatch, program)
    runner = Runner(tmp_path / "state")
    await runner.start(
        Opening("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": os.environ["PATH"]},
        args=["--no-context-files"],
        extension=EXTENSION,
    )
    return runner


@pytest.mark.anyio
async def test_the_room_is_given_the_tools_the_installed_cli_actually_has(
    tmp_path, monkeypatch
):
    """Read off the machine's own CLI, not shipped as a list from the backend.

    A catalog assembled by the backend would be a claim about a file the backend
    cannot see: a machine still holding an older CLI would offer the agent a
    tool whose command is not there.
    """
    runner = await with_tools(tmp_path, monkeypatch)
    try:
        spec = json.loads(
            (runner.state / "extension/platform.json").read_text(encoding="utf-8")
        )
        assert not spec["unavailable"]
        assert spec["socket"] == socket_path(runner.state)
        published = {tool["name"] for tool in spec["tools"]}
        # The platform's table, under the names every harness uses, and the
        # commands that have to run here as a process.
        assert {"chat_send", "cheese_doc_get", "cheese_recall"} <= published
        assert {"cheese_worktree", "cheese_sync"} <= published
        assert "cheese_chat_send" not in published
        worktree = next(t for t in spec["tools"] if t["name"] == "cheese_worktree")
        # The CLI's own help, so the description cannot drift from the command.
        assert worktree["inputSchema"]["properties"]["task_id"]["description"]
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_machine_without_the_cli_still_opens_and_says_why(
    tmp_path, monkeypatch
):
    """The tools are one of five things this extension is for. A room that lost
    them keeps the other four, and records the reason where a launch failure is
    read — from the inside, no tools looks exactly like a harness that was never
    given any, and the agent concludes it should shell out."""
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    runner = Runner(tmp_path / "state")
    await runner.start(
        Opening("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": os.environ["PATH"]},
        args=["--no-context-files"],
        extension=EXTENSION,
    )
    try:
        spec = json.loads(
            (runner.state / "extension/platform.json").read_text(encoding="utf-8")
        )
        assert spec["tools"] == []
        assert "cheese" in spec["unavailable"]
        assert (runner.state / "extension/index.ts").is_file()
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_tool_call_runs_the_command_that_tool_names(tmp_path, monkeypatch):
    runner = await with_tools(tmp_path, monkeypatch, ECHOING_CLI)
    work = tmp_path / "work"
    work.mkdir()
    try:
        result = await call(
            runner.state,
            "cli",
            {
                "tool": "cheese_sync",
                "arguments": {"note": "第一版好了", "task": "t-1"},
                "cwd": str(work),
            },
        )
        assert result["status"] == 0
        ran = json.loads(result["stdout"])
        assert ran["argv"] == ["sync", "--task=t-1", "--", "第一版好了"]
        # Where the agent is working, not where the runner happens to be: half
        # of what the CLI does is about this checkout.
        assert ran["cwd"] == str(work)
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_call_the_cli_would_refuse_is_refused_without_ending_the_session(
    tmp_path, monkeypatch
):
    """argparse answers a bad command line by exiting the process.

    `SystemExit` is not an `Exception`, so left alone it walks out through the
    handler meant to report it and takes the runner's event loop with it — a
    model that guessed one field wrong would end the room's session. What comes
    back instead is what the CLI would have printed, and the session is still
    answering afterwards.
    """
    runner = await with_tools(tmp_path, monkeypatch, ECHOING_CLI)
    try:
        with pytest.raises(RuntimeError, match="Unknown Cheese tool"):
            await call(runner.state, "cli", {"tool": "cheese_nope", "arguments": {}})
        with pytest.raises(RuntimeError, match="required"):
            await call(
                runner.state, "cli", {"tool": "cheese_worktree", "arguments": {}}
            )
        assert (await call(runner.state, "ping"))["alive"] is True
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_platform_tool_runs_against_the_backend_not_the_cli(
    tmp_path, monkeypatch
):
    """A tool from the table is not a command line: it goes to the backend with
    the room's credentials, and its answer comes back as text."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.path, self.headers["X-Cheese-Token"], body))
            answer = json.dumps({"data": {"id": "m-9", "content": body["content"]}})
            self.send_response(200)
            self.send_header("Content-Length", str(len(answer.encode())))
            self.end_headers()
            self.wfile.write(answer.encode())

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    monkeypatch.setenv("CHEESE_API", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("CHEESE_TOKEN", "room-token")
    monkeypatch.setenv("CHEESE_TOPIC", "room")
    monkeypatch.setenv("NO_PROXY", "*")
    runner = await with_tools(tmp_path, monkeypatch)
    try:
        result = await call(
            runner.state,
            "cli",
            {"tool": "chat_send", "arguments": {"content": "第一版好了"}},
        )
        assert result["status"] == 0, result
        assert json.loads(result["stdout"])["id"] == "m-9"
        [(path, token, body)] = received
        assert (path, token, body["content"]) == (
            "/topics/room/messages",
            "room-token",
            "第一版好了",
        )
    finally:
        await runner.close()
        server.shutdown()
        server.server_close()
        thread.join()
