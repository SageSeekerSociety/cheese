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
# calls come back here. Both the catalog and the argv come from the CLI's own
# argparse tree, which is the point: a tool exists exactly when the command
# does, and takes exactly what the command takes.

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


def build_parser():
    p = argparse.ArgumentParser(prog="cheese")
    sub = p.add_subparsers(dest="cmd", required=True)
    chat = sub.add_parser("chat").add_subparsers(dest="chatcmd", required=True)
    send = chat.add_parser("send", description="发布消息")
    send.add_argument("content", nargs="?")
    send.add_argument("--reply-to")
    doc = sub.add_parser("doc").add_subparsers(dest="doccmd", required=True)
    read = doc.add_parser("get", description="读实况文档")
    read.add_argument("section")
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
        assert {"cheese_chat_send", "cheese_doc_get", "cheese_recall"} <= published
        send = next(t for t in spec["tools"] if t["name"] == "cheese_chat_send")
        properties = send["inputSchema"]["properties"]
        # The CLI's own help, so the description cannot drift from the command.
        assert properties["reply_to"]["description"]
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
                "tool": "cheese_chat_send",
                "arguments": {"content": "第一版好了", "reply_to": "m-1"},
                "cwd": str(work),
            },
        )
        assert result["status"] == 0
        ran = json.loads(result["stdout"])
        assert ran["argv"] == ["chat", "send", "--reply-to=m-1", "--", "第一版好了"]
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
            await call(runner.state, "cli", {"tool": "cheese_doc_get", "arguments": {}})
        assert (await call(runner.state, "ping"))["alive"] is True
    finally:
        await runner.close()
