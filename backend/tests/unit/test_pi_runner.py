"""The runner as the backend reaches it: over a socket, across a reconnection.

Driven against a real subprocess speaking pi's protocol rather than a fake
object, because what is being tested is the process, its pipes and its framing.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven import runner as driven_runner
from app.domain.agent.harness.driven.runner import SessionStart
from app.domain.agent.harness.pi.runner import Runner, socket_path
from app.domain.agent.harness.pi.subscription import Subscription
from app.domain.agent.nonce import new_nonce
from tests.support.hang import HANG_S
from tests.support.room_machine import NO_MACHINE

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


async def running(tmp_path, resume=None, **options):
    runner = Runner(tmp_path / "state", **options)
    session_id = await runner.start(
        SessionStart("system prompt", resume, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=["--no-context-files"],
        target=NO_MACHINE,
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
async def test_an_idle_session_is_let_go_while_a_backend_keeps_reading_it(tmp_path):
    """A backend reads every session about once a second whether anyone talks
    to it or not, so being read is not being used. The session goes, and its
    directory starts the same one again."""
    runner, session_id = await running(tmp_path, idle_exit_s=0.5)

    async def backend():
        while True:
            await call(runner.state, "entries")
            await asyncio.sleep(0.1)

    reading = asyncio.create_task(backend())
    try:
        await call(
            runner.state,
            "send",
            {"input_id": str(uuid.uuid4()), "text": "开始", "work_id": "w"},
        )
        assert runner.process is not None
        await asyncio.wait_for(runner.process.wait(), 30)
    finally:
        reading.cancel()
        await runner.close()

    again, resumed = await running(tmp_path, resume=session_id)
    try:
        assert resumed == session_id
    finally:
        await again.close()


@pytest.mark.anyio
async def test_a_session_is_idle_from_its_last_record_not_from_when_the_runner_looked(
    tmp_path, monkeypatch
):
    """How long a session has sat idle is what decides which of a person's
    sessions lets go when another starts (`host._make_room`). It counts from
    the session's last record, not from the runner's next look at its journal,
    which comes up to ``IDLE_CHECK_S`` later: a session that looked used a few
    seconds after it went quiet is kept in place of one used since."""
    monkeypatch.setattr(driven_runner, "IDLE_CHECK_S", 1.0)
    runner, _ = await running(tmp_path, idle_exit_s=60)
    listening = time.monotonic()
    try:
        await call(
            runner.state,
            "send",
            {"input_id": str(uuid.uuid4()), "text": "开始", "work_id": "w"},
        )
        # Quiet: the turn is over, and nothing is written for a while after.
        async with asyncio.timeout(HANG_S):
            while True:
                working = (await call(runner.state, "ping"))["working"]
                written = (await call(runner.state, "entries"))["entries"]
                quiet_since = time.monotonic()
                await asyncio.sleep(0.2)
                after = (await call(runner.state, "entries"))["entries"]
                if not working and after == written:
                    break
        assert quiet_since - listening < 1.0, "the turn outlasted the first look"
        # Past the runner's first look at the journal, which finds it grown.
        await asyncio.sleep(listening + 1.5 - time.monotonic())
        quiet_for = time.monotonic() - quiet_since

        idle_s = (await call(runner.state, "ping"))["idle_s"]

        assert idle_s >= quiet_for, (idle_s, quiet_for)
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_an_input_reaches_the_model_once_however_often_it_is_resent(tmp_path):
    runner, _ = await running(tmp_path)
    try:
        identifier = str(uuid.uuid4())
        work = str(uuid.uuid4())
        payload = {
            "input_id": identifier,
            "text": "改一下 greet ⟪w:00000000000000000000f001⟫",
            "work_id": work,
        }
        first = await call(runner.state, "send", payload)
        # A backend that never saw the answer resends the same id.
        assert await call(runner.state, "send", payload) == first

        entries = (await call(runner.state, "entries"))["entries"]
        assert len(entries) == len(json.loads(FIXTURE.read_text())["entries"])
        # Session metadata (model/thinking changes) belongs to no turn and
        # carries no work_id by design (FB-56); the turn's entries all
        # carry this turn's.
        worked = [entry for entry in entries if entry["type"] == "message"]
        assert worked
        assert {entry["cheese"]["work_id"] for entry in worked} == {work}
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


def _update(**event) -> dict:
    return {"event": {"type": "message_update", "assistantMessageEvent": event}}


#: One assistant message pi writes in steps, as `--mode rpc` prints it (each
#: `pause` holds it until the next prompt or steer): reasoning, then text, then
#: a tool call whose arguments stream as raw JSON, then the entry and its end.
_WRITTEN = {
    "type": "message",
    "id": "e-reply",
    "parentId": None,
    "timestamp": "2026-10-01T00:00:00.000Z",
    "message": {
        "role": "assistant",
        "timestamp": 1759276800000,
        "content": [
            {"type": "thinking", "thinking": "plan"},
            {"type": "text", "text": "Hello"},
            {
                "type": "toolCall",
                "id": "call_1",
                "name": "chat_send",
                "arguments": {"text": "Hi there"},
            },
        ],
    },
}
_WRITING = {
    "stream": [
        {"event": {"type": "agent_start"}},
        {
            "event": {
                "type": "message_start",
                "message": {"role": "assistant", "timestamp": 1759276800000},
            }
        },
        _update(type="thinking_start", contentIndex=0),
        _update(type="thinking_delta", contentIndex=0, delta="plan"),
        _update(type="text_start", contentIndex=1),
        _update(type="text_delta", contentIndex=1, delta="Hel"),
        {"pause": True},
        _update(type="text_delta", contentIndex=1, delta="lo"),
        _update(type="text_end", contentIndex=1, content="Hello"),
        _update(
            type="toolcall_start", contentIndex=2, id="call_1", toolName="chat_send"
        ),
        _update(type="toolcall_delta", contentIndex=2, delta='{"text": "Hi'),
        {"pause": True},
        _update(type="toolcall_delta", contentIndex=2, delta=' there"}'),
        {"pause": True},
        {"entry": _WRITTEN},
        {"event": {"type": "message_end", "message": _WRITTEN["message"]}},
        {"event": {"type": "agent_end", "messages": []}},
        {"event": {"type": "agent_settled"}},
    ]
}


@pytest.mark.anyio
async def test_what_pi_writes_while_an_earlier_message_lands_stays_shown(tmp_path):
    """The person's message ending makes the runner pull the journal, and pi can
    start writing the answer before that page is back. Landing the person's
    message must not take the answer's first words off the screen with it: they
    would stay gone until pi wrote more, which may be a long while."""
    text = f"say it {new_nonce()}"
    user = {"role": "user", "content": [{"type": "text", "text": text}]}
    user_entry = {
        "type": "message",
        "id": "e-user",
        "parentId": None,
        "timestamp": "2026-10-01T00:00:00.000Z",
        "message": user,
    }
    recording = tmp_path / "racing.json"
    recording.write_text(
        json.dumps(
            {
                "stream": [
                    {"event": {"type": "agent_start"}},
                    {"event": {"type": "message_start", "message": user}},
                    {"entry": user_entry},
                    {
                        "before_next_page": [
                            {
                                "type": "message_start",
                                "message": {"role": "assistant", "timestamp": 1},
                            },
                            {
                                "type": "message_update",
                                "assistantMessageEvent": {
                                    "type": "text_start",
                                    "contentIndex": 0,
                                },
                            },
                            {
                                "type": "message_update",
                                "assistantMessageEvent": {
                                    "type": "text_delta",
                                    "contentIndex": 0,
                                    "delta": "First words.",
                                },
                            },
                        ]
                    },
                    {"event": {"type": "message_end", "message": user}},
                    {"pause": True},
                ]
            }
        )
    )
    binary = tmp_path / "pi"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {recording} "$@"\n')
    binary.chmod(0o700)
    runner = Runner(tmp_path / "state")
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=str(binary),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
        target=NO_MACHINE,
    )
    try:
        await call(
            runner.state,
            "send",
            {"input_id": str(uuid.uuid4()), "text": text, "work_id": str(uuid.uuid4())},
        )
        # The person's message has landed: the page that was on its way is in.
        deadline = time.monotonic() + 10
        while not (await call(runner.state, "entries"))["entries"]:
            assert time.monotonic() < deadline, "the person's message never landed"
            await asyncio.sleep(0.05)

        live = (await call(runner.state, "entries", {"wait": 0, "live": None}))["live"]
    finally:
        await runner.close()

    assert live["blocks"] == [{"type": "text", "text": "First words."}]


@pytest.mark.anyio
async def test_the_message_pi_is_writing_is_shown_as_it_grows_and_never_kept(
    tmp_path,
):
    text = f"go on {new_nonce()}"
    user_entry = {
        "type": "message",
        "id": "e-user",
        "parentId": None,
        "timestamp": "2026-10-01T00:00:00.000Z",
        "message": {
            "role": "user",
            "content": [{"type": "text", "text": text}],
        },
    }
    recording = tmp_path / "writing.json"
    recording.write_text(
        json.dumps(
            {
                "stream": [
                    {"entry": user_entry},
                    {"pause": True},
                    *_WRITING["stream"],
                ]
            }
        )
    )
    binary = tmp_path / "pi"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {recording} "$@"\n')
    binary.chmod(0o700)
    runner = Runner(tmp_path / "state")
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=str(binary),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
        target=NO_MACHINE,
    )
    work = str(uuid.uuid4())

    async def shown(seen: str) -> dict:
        answer = await call(runner.state, "entries", {"wait": 10, "live": seen})
        assert "live" in answer, "nothing new was shown"
        return answer["live"]

    async def step(method: str) -> None:
        await call(
            runner.state,
            method,
            {"input_id": str(uuid.uuid4()), "text": text, "work_id": work},
        )

    try:
        mark = runner.live_mark()
        await step("send")
        # Consume the native user entry before the assistant starts writing.
        await call(runner.state, "entries")
        await step("steer")
        live = await shown(mark)
        assert live["work_id"] == work
        # That it reasoned shows; what it reasoned does not.
        assert live["blocks"] == [{"type": "thinking"}, {"type": "text", "text": "Hel"}]

        await step("steer")
        live = await shown(live["mark"])
        assert live["blocks"] == [
            {"type": "thinking"},
            {"type": "text", "text": "Hello"},
            {
                "type": "tool",
                "id": "call_1",
                "name": "chat_send",
                "arguments": '{"text": "Hi',
            },
        ]

        await step("steer")
        live = await shown(live["mark"])
        assert json.loads(live["blocks"][2]["arguments"]) == {"text": "Hi there"}

        await step("steer")
        while live["blocks"]:
            live = await shown(live["mark"])
        entries = (await call(runner.state, "entries"))["entries"]
    finally:
        await runner.close()

    # The finished message is the record; what was shown of it never was.
    assert [entry["id"] for entry in entries] == ["e-user", "e-reply"]


@pytest.mark.anyio
async def test_a_record_of_the_runners_own_is_news_once(tmp_path):
    """The runner writes records of its own into the log (a compaction, a
    failed call's verdict). Once a backend has them, a read that waits is not
    answered with them again: it waits for something new."""
    text = f"go {new_nonce()}"
    work = str(uuid.uuid4())
    recording = tmp_path / "compacting.json"
    recording.write_text(
        json.dumps(
            {
                "stream": [
                    {
                        "entry": {
                            "type": "message",
                            "id": "e-user",
                            "parentId": None,
                            "timestamp": "2026-10-01T00:00:00.000Z",
                            "message": {
                                "role": "user",
                                "content": [{"type": "text", "text": text}],
                            },
                        }
                    },
                    {"pause": True},
                    {"event": {"type": "agent_start"}},
                    {"event": {"type": "compaction_start", "reason": "threshold"}},
                    {"event": {"type": "compaction_end", "reason": "threshold"}},
                    {"event": {"type": "agent_settled"}},
                ]
            }
        )
    )
    binary = tmp_path / "pi"
    binary.write_text(f'#!/bin/sh\nexec {sys.executable} {FAKE} {recording} "$@"\n')
    binary.chmod(0o700)
    runner = Runner(tmp_path / "state")
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=str(binary),
        cwd=str(tmp_path),
        env={"PATH": "/usr/bin:/bin"},
        args=[],
        target=NO_MACHINE,
    )
    landed: list = []

    async def consume(*args):
        landed.append(args[3])

    async def activity(*_):
        pass

    mirror = Subscription(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "teammate", harness="pi"),
        tmp_path / "mirror" / "entries.sqlite",
        lambda method, params: call(runner.state, method, params),
        consume,
        activity,
    )
    mirror.path.parent.mkdir()
    try:
        await call(
            runner.state,
            "send",
            {"input_id": str(uuid.uuid4()), "text": text, "work_id": work},
        )
        # Consume only the user prelude; compaction starts with the next steer.
        await mirror.drain()
        landed.clear()
        await call(
            runner.state,
            "steer",
            {"input_id": str(uuid.uuid4()), "text": "go", "work_id": work},
        )
        async with asyncio.timeout(10):
            while not any(
                entry.get("type") == "cheese_compacting"
                for entry in (await call(runner.state, "entries"))["entries"]
            ):
                await asyncio.sleep(0.05)
        await mirror.drain(wait=1.0)
        assert landed

        started = time.monotonic()
        await mirror.drain(wait=1.0)
        assert time.monotonic() - started >= 0.8
    finally:
        await mirror.release()
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
# pi has no MCP client, so a room's platform tools reach it as extension tools
# whose calls come back here. The catalog is the platform's own CLI file,
# shipped with the runner: its tool table, which runs here against the backend,
# and its argparse tree, whose commands run as the CLI on the room's machine — a
# command exists exactly when the CLI has it, and takes exactly what it takes.

# Shape, not content: what the runner does with these files is write them
# where pi and the extension will look.
EXTENSION = {"index.ts": "export default function () {}\n"}


async def with_tools(tmp_path, target=NO_MACHINE):
    runner = Runner(tmp_path / "state")
    await runner.start(
        SessionStart("system prompt", None, agent_handle="teammate"),
        binary=shim(tmp_path),
        cwd=str(tmp_path),
        env={"PATH": os.environ["PATH"]},
        args=["--no-context-files"],
        target=target,
        extension=EXTENSION,
    )
    return runner


@pytest.mark.anyio
async def test_the_room_is_given_the_platforms_tools_and_no_cli_command(tmp_path):
    """The CLI's commands run as `cheese …` in the room's bash, as on every
    other harness; none of them is a tool of its own."""
    runner = await with_tools(tmp_path)
    try:
        spec = json.loads(
            (runner.state / "extension/platform.json").read_text(encoding="utf-8")
        )
        assert not spec["unavailable"]
        assert spec["socket"] == socket_path(runner.state)
        published = {tool["name"] for tool in spec["tools"]}
        # The platform's table, under the names every harness uses.
        assert {"chat_send", "cheese_doc", "cheese_notify"} <= published
        assert not {"cheese_worktree", "cheese_sync"} & published
        assert "cheese_chat_send" not in published
        # And what a call to one as a tool is told instead of pi's "not found".
        assert "`cheese push-fix …`" in spec["commands"]["cheese_push_fix"]
        assert not set(spec["commands"]) & published
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_cli_the_runner_cannot_read_still_leaves_the_room_its_tools(
    tmp_path, monkeypatch
):
    """The commands' names are read out of the CLI's parser for the sentence a
    call to one as a tool is told. A parser that cannot be read costs that
    sentence (pi's own "not found" stands), never the platform's tools."""
    from app.domain.agent.harness.pi import catalog

    def unreadable():
        raise AttributeError("no parser")

    monkeypatch.setattr(catalog, "cli_commands", unreadable)
    runner = await with_tools(tmp_path)
    try:
        spec = json.loads(
            (runner.state / "extension/platform.json").read_text(encoding="utf-8")
        )
        assert not spec["unavailable"]
        assert "chat_send" in {tool["name"] for tool in spec["tools"]}
        assert spec["commands"] == {}
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_what_the_room_is_told_to_publish_with_is_a_tool_the_room_has(
    tmp_path,
):
    """The prompt names the publishing tool on every turn. A session given no
    tool by that name is told to do the one thing it cannot, and says nothing
    to anybody."""
    from app.domain.agent.harness import prompt

    published = prompt.publication_prompt("x")
    named = {word.strip("`. ") for word in published.split() if "chat_send" in word}
    assert named, "the room's prompt no longer names a publishing tool"
    runner = await with_tools(tmp_path)
    try:
        spec = json.loads(
            (runner.state / "extension/platform.json").read_text(encoding="utf-8")
        )
        assert named <= {tool["name"] for tool in spec["tools"]}
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_cli_command_called_as_a_tool_is_refused_and_the_session_goes_on(
    tmp_path,
):
    """A call that reaches the runner under a name the room was never given
    (the extension sends only its registered tools) is no such tool, and the
    session is still answering afterwards. What a model that calls a CLI
    command as a tool reads is pi's result, rewritten by the extension
    (`commands` in the manifest; tests/extension/platform.test.ts)."""
    runner = await with_tools(tmp_path)
    try:
        result = await call(
            runner.state, "cli", {"tool": "cheese_worktree", "arguments": {}}
        )
        assert result["status"] == 1
        assert result["stderr"] == "cheese_worktree: no such tool"
        assert (await call(runner.state, "ping"))["alive"] is True
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_platform_tool_runs_against_the_backend_and_takes_no_machine(
    tmp_path, monkeypatch
):
    """A tool from the table is not a command line: it goes to the backend with
    the room's credentials, its answer comes back as text, and a session that
    has no machine yet is not made to take one for it."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    received = []
    runs = []

    class API(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append(
                (self.path, self.headers["Authorization"].removeprefix("Bearer "), body)
            )
            runs.append(self.headers.get("X-Cheese-Run"))
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
    runner = await with_tools(
        tmp_path, {**NO_MACHINE, "lease_path": "/topics/room/sessions/s/work-lease"}
    )
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
        # pi's runner reports no session lifecycle, so it names no run.
        assert runs == [None]
    finally:
        await runner.close()
        server.shutdown()
        server.server_close()
        thread.join()


# --- the session compacting itself ----------------------------------------------


@pytest.mark.anyio
async def test_an_idle_session_compacts_itself_through_pi(tmp_path):
    """`cheese_session(action=compact)` reaches this runner as `request_compact`, and the ask
    goes to pi as its own `compact` command. Nothing is running, so it goes at
    once, and what the room reads is the compaction pi reported."""
    runner, _ = await running(tmp_path)
    try:
        assert runner.request_compact() == "requested"
        for _ in range(200):
            if not runner.compact_request:
                break
            await asyncio.sleep(0.02)

        stats = await runner.client.request("get_session_stats")
        assert stats["compactions"] == 1
        entries = (await call(runner.state, "entries"))["entries"]
        assert any(entry["type"] == "cheese_compacting" for entry in entries)
    finally:
        await runner.close()


@pytest.mark.anyio
async def test_a_request_made_mid_turn_waits_for_that_turn_to_settle(tmp_path):
    """pi's `compact` aborts whatever operation it finds running, so the ask
    waits for the turn that made it to settle — and twice in one turn asks
    once, because one request already waiting is one request."""
    runner, _ = await running(tmp_path)
    try:
        runner.working = True
        assert runner.request_compact() == "requested"
        await asyncio.sleep(0.05)
        stats = await runner.client.request("get_session_stats")
        assert stats["compactions"] == 0
        assert runner.request_compact() == "already"

        # The turn settles; the request goes then.
        await runner.observe({"type": "agent_settled"})
        for _ in range(200):
            if not runner.compact_request:
                break
            await asyncio.sleep(0.02)
        stats = await runner.client.request("get_session_stats")
        assert stats["compactions"] == 1
    finally:
        await runner.close()
