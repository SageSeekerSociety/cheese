"""The Claude Code runner, driving the pinned build against the model fixture.

The runner is what a screen runs: it starts `claude -p` with stream-json on both
pipes, journals every line it reads stamped with the room's work it belongs to,
and answers the backend on a socket. None of the stamping can be checked against
a stand-in, because what it keys on — when the build echoes an input, when a
turn it started for itself begins, which agents only their own file reports —
is the build's behaviour. So these run the real archive (or, where a knob the
archive does not expose is needed, the same class in-process) against the real
pinned binary, and the deterministic Messages API in
`scripts/remote_execution/model_fixture.py`: a user message `DO:{...}` becomes
that tool call, anything else a plain end_turn.
"""

import asyncio
import json
import os
import shlex
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from app.domain.agent.harness.claude_code.bundle import build
from app.domain.agent.harness.claude_code.cli import LAUNCH_ARGS
from app.domain.agent.harness.claude_code.runner import Runner, end, sessions_on
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.driven.runner import socket_path
from tests.pinned_claude import claude_binary

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts/remote_execution"
AGENT = "cheese-agent"


@pytest.fixture(scope="module")
def contract():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import headless_contract

        yield headless_contract
    finally:
        sys.path.remove(str(SCRIPTS))
        sys.modules.pop("headless_contract", None)
        sys.modules.pop("model_fixture", None)


class Machine:
    """An isolated HOME with a git workspace, and a model fixture to talk to."""

    def __init__(self, contract, root: Path, handler=None):
        self.contract = contract
        self.root = root
        self.home = root / "home"
        self.config = self.home / ".claude"
        self.workspace = self.home / "workspace"
        self.config.mkdir(parents=True)
        self.workspace.mkdir()
        contract.workspace(self.workspace)
        (root / "tmp").mkdir()
        (root / "fixture").mkdir()
        self.state = root / "state"
        self.server = contract.Server(("127.0.0.1", 0), handler or contract.Handler)
        self.server.state = {
            "dir": root / "fixture",
            "actions": contract.Directives(),
            "requests": [],
        }
        self.serving = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.serving.start()
        self.env = contract.fixture_env(self.home, root, self.server.server_port)
        self.command = shlex.join(
            [
                claude_binary(),
                "--model",
                contract.MODEL,
                "--setting-sources",
                "user",
                *LAUNCH_ARGS,
            ]
        )

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def machine(contract, tmp_path):
    made = Machine(contract, tmp_path)
    try:
        yield made
    finally:
        made.close()


class Screen:
    """The runner archive, started the way the launcher starts it."""

    def __init__(self, machine: Machine, env: dict[str, str] | None = None):
        self.machine = machine
        artifact = machine.root / "runner.pyz"
        artifact.write_bytes(build())
        self.errors = (machine.root / "runner.log").open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-S", str(artifact), "--state", str(machine.state)],
            cwd=machine.workspace,
            env={
                **machine.env,
                "CHEESE_CLAUDE_COMMAND": machine.command,
                "CHEESE_AUTHOR": AGENT,
                **(env or {}),
            },
            stdout=subprocess.DEVNULL,
            stderr=self.errors,
        )

    def call(self, method, params=None, timeout=60.0):
        deadline = time.monotonic() + timeout
        while True:
            client = socket.socket(socket.AF_UNIX)
            client.settimeout(timeout)
            try:
                client.connect(socket_path(self.machine.state))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                client.close()
                assert self.process.poll() is None, self.log()
                assert time.monotonic() < deadline, "the runner never listened"
                time.sleep(0.1)
        with client:
            client.sendall(
                json.dumps({"method": method, "params": params or {}}).encode() + b"\n"
            )
            answer = json.loads(client.makefile("rb").readline())
        assert "error" not in answer, answer
        return answer["result"]

    def records(self) -> list[dict]:
        found, after = [], 0
        while True:
            page = self.call("events", {"after": after})["events"]
            found += page
            if len(page) < PAGE:
                return found
            after = page[-1]["sequence"]

    def wait(self, predicate, timeout=90.0, after=0) -> dict:
        """The first journal entry after sequence ``after`` that matches."""
        deadline = time.monotonic() + timeout
        while True:
            for entry in self.records():
                if entry["sequence"] > after and predicate(entry["record"]):
                    return entry
            assert time.monotonic() < deadline, json.dumps(
                [e["record"].get("type") for e in self.records()]
            )
            time.sleep(0.2)

    def send(self, text, *, work_id, method="send") -> str:
        identifier = str(uuid.uuid4())
        self.call(method, {"input_id": identifier, "work_id": work_id, "text": text})
        return identifier

    def log(self) -> str:
        self.errors.flush()
        return (self.machine.root / "runner.log").read_text()

    def stop(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(20)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.errors.close()


@pytest.fixture
def screen(machine):
    started = Screen(machine)
    try:
        yield started
    finally:
        started.stop()


def _is(kind, subtype=None):
    def match(record):
        return record.get("type") == kind and (
            subtype is None or record.get("subtype") == subtype
        )

    return match


def _blocks(record):
    content = (record.get("message") or {}).get("content")
    return content if isinstance(content, list) else []


def _tool_result(record) -> bool:
    return (
        record.get("type") == "user"
        and not record.get("isReplay")
        and any(block.get("type") == "tool_result" for block in _blocks(record))
    )


def _echo_of(identifier):
    return lambda r: r.get("type") == "user" and r.get("uuid") == identifier


def _turn(records, start):
    """Entries from ``start`` to the first ``result`` at or after it."""
    turn = []
    for entry in records:
        if entry["sequence"] >= start:
            turn.append(entry)
            if entry["record"].get("type") == "result":
                return turn
    raise AssertionError("the turn never closed")


# --- 1. a send opens a turn for its work, and a steer lands inside it ----------


def test_a_send_opens_a_turn_for_its_work_and_result_closes_it(screen, contract):
    work = str(uuid.uuid4())
    sent = screen.send("hello", work_id=work)
    result = screen.wait(_is("result"))
    # The build taking this input is the moment its turn begins.
    opened = screen.wait(
        lambda r: (
            r.get("type") == "command_lifecycle"
            and r.get("state") == "started"
            and r.get("command_uuid") == sent
        )
    )
    echo = screen.wait(_echo_of(sent))

    owner = {"harness": "claude-code", "agent_handle": AGENT, "work_id": work}
    assert opened["record"]["cheese"] == {**owner, "turn_start": True}
    assert echo["record"]["isReplay"] is True
    assert echo["record"]["cheese"] == {
        **owner,
        "receipt": True,
        "receipt_work_id": work,
        "receipt_execution_work_id": work,
        "receipt_session_id": screen.call("ping")["session_id"],
    }
    turn = _turn(screen.records(), opened["sequence"])
    assert turn[-1] == result
    assert {e["record"]["cheese"].get("work_id") for e in turn} == {work}
    assert not any(e["record"]["cheese"].get("unsolicited") for e in turn)
    assert sum(bool(e["record"]["cheese"].get("turn_start")) for e in turn) == 1
    assert result["record"]["is_error"] is False
    assert screen.call("ping")["working"] is False


def test_a_steer_is_received_only_at_the_tool_boundary(screen, contract):
    work = str(uuid.uuid4())
    screen.send(
        contract.do("Bash", command="sleep 4; echo FIRST_DONE", description="sleep"),
        work_id=work,
    )
    screen.wait(_is("system", "task_started"))
    assert screen.call("ping")["work_id"] == work
    steer = screen.send("STEER_MARKER", work_id=work, method="steer")
    echo = screen.wait(_echo_of(steer))
    boundary = screen.wait(_tool_result)
    result = screen.wait(_is("result"), after=echo["sequence"])

    # Taken inside the turn it was said to, once the running tool returned.
    assert boundary["sequence"] < echo["sequence"] < result["sequence"]
    assert echo["record"]["cheese"]["receipt"] is True
    assert "turn_start" not in echo["record"]["cheese"]
    assert echo["record"]["cheese"]["work_id"] == work
    assert result["record"]["cheese"]["work_id"] == work
    results = [e for e in screen.records() if _is("result")(e["record"])]
    assert len(results) == 1


# --- 2. a turn the session starts for itself ------------------------------------


def test_a_turn_the_session_starts_itself_is_unsolicited_with_its_own_work(
    screen, contract
):
    work = str(uuid.uuid4())
    screen.send(
        contract.do(
            "Bash",
            command="sleep 2; echo BACKGROUND_DONE",
            description="background",
            run_in_background=True,
        ),
        work_id=work,
    )
    first = screen.wait(_is("result"))
    notified = screen.wait(_is("system", "task_notification"), after=first["sequence"])
    follow = screen.wait(_is("result"), after=first["sequence"])

    assert first["record"]["cheese"]["work_id"] == work
    opened = next(
        e
        for e in screen.records()
        if e["sequence"] > first["sequence"] and e["record"]["cheese"].get("turn_start")
    )
    turn = _turn(screen.records(), opened["sequence"])
    stamps = {e["record"]["cheese"].get("work_id") for e in turn}
    (own,) = stamps
    assert own != work
    uuid.UUID(own)
    assert all(e["record"]["cheese"].get("unsolicited") for e in turn)
    assert turn[-1] == follow
    assert notified["sequence"] < follow["sequence"]


# --- 3. a platform command is nobody's work --------------------------------------


def test_a_platform_command_produces_no_room_owned_records(screen):
    work = str(uuid.uuid4())
    screen.send("hello", work_id=work)
    before = screen.wait(_is("result"))["sequence"]

    answer = screen.call("command", {"text": "/reload-skills"}, timeout=150)

    assert answer["is_error"] is False
    opened = screen.wait(
        lambda r: (
            r.get("type") == "command_lifecycle"
            and r.get("state") == "started"
            and str(r.get("command_uuid")).startswith("cheese-command-")
        ),
        after=before,
    )
    during = _turn(screen.records(), opened["sequence"])
    assert len(during) > 1
    for entry in during:
        assert "work_id" not in entry["record"]["cheese"], entry
        assert "unsolicited" not in entry["record"]["cheese"], entry
        assert "receipt" not in entry["record"]["cheese"], entry


# --- 4. what only the files on disk hold ----------------------------------------


def test_nested_and_workflow_agents_are_read_from_their_own_files(screen, contract):
    do = contract.do
    inner = do(
        "Agent",
        description="grandchild",
        subagent_type="general-purpose",
        run_in_background=False,
        prompt=do("Bash", command="echo NESTED_RAN", description="grandchild echo"),
    )
    screen.send(
        do(
            "Agent",
            description="child",
            subagent_type="general-purpose",
            run_in_background=False,
            prompt=inner,
        ),
        work_id=str(uuid.uuid4()),
    )
    first = screen.wait(_is("result"), timeout=150)
    started = [
        e["record"]
        for e in screen.records()
        if _is("system", "task_started")(e["record"])
        and e["record"].get("task_type") == "local_agent"
    ]
    assert len(started) == 2, started
    main_calls = {
        block["id"]
        for e in screen.records()
        if e["record"].get("type") == "assistant"
        and e["record"].get("parent_tool_use_id") is None
        for block in _blocks(e["record"])
        if block.get("type") == "tool_use"
    }
    (nested,) = [r for r in started if r["tool_use_id"] not in main_calls]

    def ran(agent, marker):
        return lambda r: (
            r.get("type") == "cheese_file"
            and r.get("agent_id") == agent
            and marker in json.dumps(r["entry"])
        )

    screen.wait(ran(nested["task_id"], "NESTED_RAN"))

    script = (
        "export const meta = { name: 'contract', description: 'contract probe' }\n"
        "await agent("
        + json.dumps(do("Bash", command="echo WORKFLOW_RAN", description="echo"))
        + ", { label: 'one' })\n"
    )
    screen.send(do("Workflow", script=script), work_id=str(uuid.uuid4()))
    progress = screen.wait(
        lambda r: _is("system", "task_progress")(r) and r.get("workflow_progress"),
        timeout=150,
        after=first["sequence"],
    )
    (agent,) = {
        entry["agentId"]
        for entry in progress["record"]["workflow_progress"]
        if entry.get("agentId")
    }
    tailed = screen.wait(ran(agent, "WORKFLOW_RAN"), timeout=150)
    assert set(tailed["record"]["entry"]) <= {
        "type",
        "uuid",
        "timestamp",
        "agentId",
        "isSidechain",
        "message",
    }


# --- 5. interrupt ------------------------------------------------------------


def test_an_interrupt_marks_the_result_it_ends(screen, contract):
    work = str(uuid.uuid4())
    screen.send(
        contract.do("Bash", command="sleep 60; echo LATE", description="long"),
        work_id=work,
    )
    screen.wait(_is("system", "task_started"))

    assert screen.call("interrupt") == {"interrupted": True}

    result = screen.wait(_is("result"))
    assert result["record"]["cheese"]["interrupted"] is True
    assert result["record"]["cheese"]["work_id"] == work
    assert screen.call("ping")["working"] is False


def test_an_error_ends_the_work_though_a_background_task_runs_on(contract, tmp_path):
    """A turn the API killed is over; a task it left behind does not undo that.

    An unfinished input shuts the seat, and the only thing that opens it again
    is the terminal stamp on the record that ended the work. The build can only
    ever write it there — the next record belongs to a new work — so a session
    that stays quiet keeps its seat shut for good: the platform defers every
    later message, no turn runs, and the room is answered by silence.

    A task still running is an interval of its own: it can never settle the
    inputs of the work that ended, while the hold on those inputs is what stops
    anyone resending them. The clean completion above takes no account of such
    tasks either.
    """

    class Refusing(contract.Handler):
        """The script answers its first request, then the budget is gone."""

        def do_POST(self):
            if not self.server.state["requests"]:
                # Nothing answered yet: this is the request the script serves.
                return super().do_POST()
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            data = json.dumps(
                {
                    "type": "error",
                    "error": {
                        "type": "rate_limit_error",
                        "message": "Budget has been exceeded!",
                    },
                }
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    machine = Machine(contract, tmp_path, handler=Refusing)
    try:
        stray = contract.do(
            "Bash",
            command="sleep 300; echo LATE",
            description="long",
            run_in_background=True,
        )
        machine.server.state["actions"] = [json.loads(stray[3:])]
        screen = Screen(machine, env={"CLAUDE_CODE_MAX_RETRIES": "1"})
        try:
            work = str(uuid.uuid4())
            sent = screen.send("hello", work_id=work)
            screen.wait(_is("system", "task_started"))
            assert screen.call("ping")["tasks"]

            result = screen.wait(_is("result"), timeout=180)
            assert result["record"]["is_error"] is True
            stamp = result["record"]["cheese"]
            assert stamp["work_terminated"] is True
            assert stamp["termination"] == "is_error"
            assert stamp["termination_work_id"] == work
            assert stamp["termination_input_ids"] == [sent]
            # The task goes on running, and the input keeps its hold: what the
            # stamp opened is the seat, not the work.
            assert screen.call("ping")["tasks"]
        finally:
            screen.stop()
    finally:
        machine.close()


# --- 6-8: the class itself, where the archive exposes no knob --------------------


def test_replacing_one_seat_does_not_stop_a_room_mates_claude(tmp_path):
    """Two teammates can keep Claude processes alive in one room home."""
    if not Path("/proc").is_dir():
        pytest.skip("requires the Linux process table used by the session host")
    stand_in = tmp_path / "bin/claude"
    stand_in.parent.mkdir()
    stand_in.symlink_to(sys.executable)
    command = [str(stand_in), "-c", "import time; time.sleep(120)"]
    config = tmp_path / ".claude"
    first = subprocess.Popen(
        command,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(config), "CHEESE_AUTHOR": "seat-a"},
    )
    second = subprocess.Popen(
        command,
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(config), "CHEESE_AUTHOR": "seat-b"},
    )
    _reaped(second)
    try:
        end(sessions_on(config, "seat-b"))
        assert second.wait(timeout=10) is not None
        assert first.poll() is None
    finally:
        for process in (first, second):
            if process.poll() is None:
                process.kill()
            process.wait()


def _reaped(process: subprocess.Popen) -> None:
    """Reap ``process`` the moment it exits. It is this test's child, so once it
    ends it stays a zombie that `end` still finds alive, and `end` would wait
    out its SIGTERM and SIGKILL graces for it; the claude a runner replaces is
    no child of the runner's, and leaves no zombie."""
    threading.Thread(target=process.wait, daemon=True).start()


async def _started(machine, monkeypatch, **options) -> Runner:
    # The build runs where it was started: the isolated workspace, not the repo.
    monkeypatch.chdir(machine.workspace)
    runner = Runner(machine.state, **options)
    await runner.start(
        command=machine.command, env=machine.env, resume=None, agent_handle=AGENT
    )
    return runner


@pytest.mark.anyio
async def test_starting_one_seat_does_not_end_another_seat_on_the_same_config_dir(
    machine, monkeypatch, tmp_path
):
    """Only a previous process for this seat may be replaced at startup."""
    stand_in = tmp_path / "bin/claude"
    stand_in.parent.mkdir()
    # A symlink keeps Python's libraries reachable under the process name.
    stand_in.symlink_to(sys.executable)
    sleep = [str(stand_in), "-c", "import time; time.sleep(120)"]
    same = subprocess.Popen(
        sleep,
        env={
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(machine.config),
            "CHEESE_AUTHOR": AGENT,
        },
    )
    room_mate = subprocess.Popen(
        sleep,
        env={
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(machine.config),
            "CHEESE_AUTHOR": "cheese-room-mate",
        },
    )
    other = subprocess.Popen(
        sleep,
        env={
            **os.environ,
            "CLAUDE_CONFIG_DIR": str(tmp_path / "elsewhere"),
            "CHEESE_AUTHOR": AGENT,
        },
    )
    _reaped(same)
    try:
        runner = await _started(machine, monkeypatch)
        try:
            assert same.wait(timeout=10) is not None
            assert room_mate.poll() is None
            assert other.poll() is None
        finally:
            await runner.close()
    finally:
        for process in (same, room_mate, other):
            process.kill()
            process.wait()


async def _results(runner: Runner, count: int) -> None:
    async with asyncio.timeout(90):
        while sum(_is("result")(e["record"]) for e in runner.journal.read(0)) < count:
            await asyncio.sleep(0.2)


@pytest.mark.anyio
async def test_an_idle_session_is_let_go_while_a_backend_keeps_reading_it(
    machine, monkeypatch
):
    """A backend reads every session about once a second whether anyone talks
    to it or not, so being read is not being used."""
    runner = await _started(machine, monkeypatch, idle_exit_s=0.5)

    async def backend():
        while True:
            await runner.dispatch("events", {"after": 0})
            await asyncio.sleep(0.1)

    reading = asyncio.create_task(backend())
    try:
        await runner.send(str(uuid.uuid4()), "hello", work_id=str(uuid.uuid4()))
        await _results(runner, 1)
        assert runner.process is not None
        # Closing stdin is the build's own way out: a clean exit, not a kill.
        assert await asyncio.wait_for(runner.process.wait(), 60) == 0
    finally:
        reading.cancel()
        await runner.close()


@pytest.mark.anyio
async def test_the_message_after_an_idle_exit_continues_the_same_conversation(
    machine, monkeypatch
):
    runner = await _started(machine, monkeypatch, idle_exit_s=0.5)
    try:
        await runner.send(
            str(uuid.uuid4()), "The password is PINEAPPLE.", work_id=str(uuid.uuid4())
        )
        await _results(runner, 1)
        assert runner.process is not None
        await asyncio.wait_for(runner.process.wait(), 60)
    finally:
        await runner.close()

    # What the next message does: the same state directory, started again.
    again = await _started(machine, monkeypatch)
    try:
        await again.send(
            str(uuid.uuid4()), "What was the password?", work_id=str(uuid.uuid4())
        )
        await _results(again, 2)
    finally:
        await again.close()
    asked = json.dumps(machine.server.state["requests"][-1]["messages"])
    assert "PINEAPPLE" in asked
    assert "What was the password?" in asked


@pytest.mark.anyio
async def test_a_question_the_build_would_ask_never_reaches_the_driver(
    machine, monkeypatch, contract
):
    """AskUserQuestion is not offered: a model that calls it anyway gets an
    ordinary tool error, and nothing arrives on stdout for the runner to refuse
    — the turn ends by itself instead of waiting on a person."""
    runner = await _started(machine, monkeypatch)
    written = []
    write = runner._write

    async def recording(message):
        written.append(message)
        await write(message)

    runner._write = recording
    try:
        await runner.send(
            str(uuid.uuid4()),
            contract.do(
                "AskUserQuestion",
                questions=[
                    {
                        "question": "Which one?",
                        "header": "Pick",
                        "multiSelect": False,
                        "options": [
                            {"label": "A", "description": "first"},
                            {"label": "B", "description": "second"},
                        ],
                    }
                ],
            ),
            work_id=str(uuid.uuid4()),
        )
        async with asyncio.timeout(90):
            while True:
                records = [e["record"] for e in runner.journal.read(0)]
                if any(_is("result")(r) for r in records):
                    break
                await asyncio.sleep(0.2)
    finally:
        await runner.close()

    (result,) = [r for r in records if _is("result")(r)]
    assert result["is_error"] is False
    errors = [
        block
        for r in records
        if _tool_result(r)
        for block in _blocks(r)
        if block.get("type") == "tool_result" and block.get("is_error")
    ]
    assert len(errors) == 1
    assert "No such tool available" in json.dumps(errors[0])
    # The runner wrote the message and nothing else: no refusal was needed.
    assert [message["type"] for message in written] == ["user"]


@pytest.mark.anyio
async def test_the_builds_thinking_estimate_is_not_journaled(monkeypatch, tmp_path):
    """While the model thinks, the build prints a running estimate of how much,
    one line per streamed delta. The room shows none of it, so none of it is
    kept: a long think was otherwise most of a session's journal."""
    said = [
        {"type": "system", "subtype": "init", "session_id": "s", "model": "m"},
        *(
            {
                "type": "system",
                "subtype": "thinking_tokens",
                "estimated_tokens": n,
                "estimated_tokens_delta": 1,
                "session_id": "s",
                "uuid": f"t{n}",
            }
            for n in range(1, 50)
        ),
        {
            "type": "assistant",
            "uuid": "a",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done thinking"}],
            },
        },
        {"type": "result", "subtype": "success", "is_error": False, "uuid": "r"},
    ]
    build = tmp_path / "build.py"
    build.write_text(
        "import json, sys\n"
        f"for record in {said!r}:\n"
        "    print(json.dumps(record), flush=True)\n"
        "sys.stdin.read()\n"
    )
    monkeypatch.chdir(tmp_path)
    runner = Runner(tmp_path / "state")
    await runner.start(
        command=shlex.join([sys.executable, str(build)]),
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
        resume=None,
        agent_handle=AGENT,
    )
    try:
        async with asyncio.timeout(30):
            while True:
                records = [e["record"] for e in runner.journal.read(0)]
                if any(_is("result")(r) for r in records):
                    break
                await asyncio.sleep(0.05)
    finally:
        await runner.close()

    assert [(r["type"], r.get("subtype")) for r in records] == [
        ("system", "init"),
        ("assistant", None),
        ("result", "success"),
    ]


def _streamed(event: dict) -> dict:
    return {"type": "stream_event", "parent_tool_use_id": None, "event": event}


def _delta(index: int, **delta) -> dict:
    return _streamed({"type": "content_block_delta", "index": index, "delta": delta})


#: What the build prints for one message with a text block and a tool call,
#: step by step: each step waits for a line on its stdin before the next.
_WRITING = [
    [{"type": "system", "subtype": "init", "session_id": "s", "model": "m"}],
    [
        _streamed({"type": "message_start", "message": {"id": "msg_1"}}),
        _streamed(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        ),
        _delta(0, type="text_delta", text="Hel"),
    ],
    [_delta(0, type="text_delta", text="lo")],
    [
        {
            "type": "assistant",
            "uuid": "a1",
            "parent_tool_use_id": None,
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "content": [{"type": "text", "text": "Hello"}],
            },
        },
        _streamed({"type": "content_block_stop", "index": 0}),
        _streamed(
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "mcp__cheese__chat_send",
                    "input": {},
                },
            }
        ),
        _delta(1, type="input_json_delta", partial_json='{"text": "Hi'),
    ],
    [_delta(1, type="input_json_delta", partial_json=' there"}')],
    [
        {
            "type": "assistant",
            "uuid": "a2",
            "parent_tool_use_id": None,
            "message": {
                "id": "msg_1",
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "mcp__cheese__chat_send",
                        "input": {"text": "Hi there"},
                    }
                ],
            },
        },
        _streamed({"type": "content_block_stop", "index": 1}),
        _streamed({"type": "message_stop"}),
        {"type": "result", "subtype": "success", "is_error": False, "uuid": "r"},
    ],
]


@pytest.mark.anyio
async def test_the_block_being_written_is_shown_as_it_grows_and_never_journaled(
    monkeypatch, tmp_path
):
    """With `--include-partial-messages` the build streams each block as it is
    written. A backend waiting on the runner sees the text, and a tool call's
    arguments, grow; the finished block's own record replaces it; and none of
    the deltas is kept."""
    build = tmp_path / "build.py"
    build.write_text(
        "import json, sys\n"
        f"for step in {_WRITING!r}:\n"
        "    for record in step:\n"
        "        print(json.dumps(record), flush=True)\n"
        "    sys.stdin.readline()\n"
        "sys.stdin.read()\n"
    )
    monkeypatch.chdir(tmp_path)
    runner = Runner(tmp_path / "state")
    await runner.start(
        command=shlex.join([sys.executable, str(build)]),
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
        resume=None,
        agent_handle=AGENT,
    )
    assert runner.process is not None and runner.process.stdin is not None
    stdin = runner.process.stdin

    async def shown(seen: str | None) -> dict:
        answer = await runner.dispatch(
            "events", {"after": runner.journal.last(), "wait": 10, "live": seen}
        )
        assert "live" in answer, "nothing new was shown"
        return answer["live"]

    async def step() -> None:
        stdin.write(b"\n")
        await stdin.drain()

    try:
        async with asyncio.timeout(30):
            while not runner.journal.last():
                await asyncio.sleep(0.05)
            mark = runner.live_mark()
            await step()
            live = await shown(mark)
            assert live["blocks"] == [{"type": "text", "text": "Hel"}]
            await step()
            live = await shown(live["mark"])
            assert live["blocks"] == [{"type": "text", "text": "Hello"}]
            await step()
            live = await shown(live["mark"])
            assert live["blocks"] == [
                {
                    "type": "tool",
                    "id": "toolu_1",
                    "name": "mcp__cheese__chat_send",
                    "arguments": '{"text": "Hi',
                }
            ]
            await step()
            live = await shown(live["mark"])
            assert json.loads(live["blocks"][0]["arguments"]) == {"text": "Hi there"}
            await step()
            live = await shown(live["mark"])
            assert live["blocks"] == []
            records = [e["record"] for e in runner.journal.read(0)]
    finally:
        await runner.close()

    assert [(r["type"], r.get("subtype")) for r in records] == [
        ("system", "init"),
        ("assistant", None),
        ("assistant", None),
        ("result", "success"),
    ]


# --- a session that dies on its way up says why --------------------------------


def _start_dying(tmp_path: Path, stderr: str) -> str:
    """Run the runner archive over a launch that fails before Claude Code starts.

    The launch's command is the executor client's ``bootstrap`` in front of the
    binary; this stands in for a bootstrap that prints its reason and exits, the
    way a work lease the platform refused did. Returns the runner's own log,
    which is what the backend reads once the socket has gone with the runner.
    """
    state = tmp_path / "state"
    config = tmp_path / "home/.claude"
    config.mkdir(parents=True, exist_ok=True)
    artifact = tmp_path / "runner.pyz"
    artifact.write_bytes(build())
    log = tmp_path / "runner.log"
    with log.open("ab") as errors:
        finished = subprocess.run(
            [sys.executable, "-I", "-S", str(artifact), "--state", str(state)],
            env={
                "PATH": os.environ["PATH"],
                "HOME": str(tmp_path / "home"),
                "CLAUDE_CONFIG_DIR": str(config),
                "CHEESE_CLAUDE_COMMAND": shlex.join(
                    ["sh", "-c", f"printf '%s\\n' {shlex.quote(stderr)} >&2; exit 1"]
                ),
                "CHEESE_AUTHOR": AGENT,
            },
            stdout=subprocess.DEVNULL,
            stderr=errors,
            timeout=60,
        )
    assert finished.returncode == 0
    assert not Path(socket_path(state)).exists()
    return log.read_text()


def test_a_session_that_dies_on_its_way_up_leaves_its_reason_in_the_runner_log(
    tmp_path,
):
    reason = (
        "executor_transport.PlatformHTTPError: Platform HTTP 504: "
        "工作电脑仍在准备，对话和平台工具仍可用"
    )
    log = _start_dying(tmp_path, f"Traceback (most recent call last):\n{reason}")

    assert "exited with status 1" in log
    # The backend reads the last 1200 bytes; the reason has to be in them.
    assert reason in log.encode()[-1200:].decode("utf-8", "replace")


def test_an_earlier_start_does_not_speak_for_this_one(tmp_path):
    (tmp_path / "state").mkdir(mode=0o700)
    (tmp_path / "state/claude.log").write_text("RuntimeError: yesterday's failure\n")

    log = _start_dying(tmp_path, "OSError: today's failure")

    assert "today's failure" in log
    assert "yesterday's failure" not in log


# --- a machine that attaches later brings its project's hooks ---------------


def test_the_hooks_a_helper_hands_the_session_hold_for_its_next_tool_call(
    screen, machine, contract
):
    """A session started before its machine had no project hooks to register.
    When the machine attaches, the helper that attached it (the shell prefix,
    inside a tool call) hands the session the project's hooks through this
    runner, and the next tool call fires them."""
    transport = Path(__file__).resolve().parents[2] / (
        "app/domain/agent/executor_transport.py"
    )
    log = machine.root / "project-hook.jsonl"
    hooks = {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {"type": "command", "command": f"cat >> {log}; echo >> {log}"}
                ],
            }
        ]
    }
    session = machine.root / "session"
    session.mkdir()
    attach = shlex.join(
        [
            sys.executable,
            "-c",
            "import json, runpy, sys; "
            f"module = runpy.run_path({str(transport)!r}); "
            "print('registered', module['register_project_hooks']("
            f"{{'central_config': {str(machine.config)!r}, "
            f"'target_file': {str(session / 'execution.json')!r}}}, "
            f"json.loads({json.dumps(hooks)!r})))",
        ]
    )

    def bash(command):
        mark = screen.records()[-1]["sequence"] if screen.records() else 0
        screen.send(
            contract.do("Bash", command=command, description="step"),
            work_id=str(uuid.uuid4()),
        )
        screen.wait(_is("result"), after=mark)
        return [
            block
            for entry in screen.records()
            if entry["sequence"] > mark and _tool_result(entry["record"])
            for block in _blocks(entry["record"])
            if block.get("type") == "tool_result"
        ]

    (attached,) = bash(attach)
    assert "registered True" in json.dumps(attached), attached
    assert not log.exists(), "no hooks were registered when that call started"

    bash("echo AFTER_THE_MACHINE")
    fired = [json.loads(line) for line in log.read_text().splitlines() if line]
    assert [entry["tool_input"]["command"] for entry in fired] == [
        "echo AFTER_THE_MACHINE"
    ]


# --- a new turn starts on the project's skills as they are now ------------------


def _ends(work):
    return lambda r: (
        r.get("type") == "result" and (r.get("cheese") or {}).get("work_id") == work
    )


def test_a_skill_the_project_gained_between_turns_is_offered_in_the_next(
    machine, contract
):
    """A room's session reads the project's skills through links into the
    executor's view, which no file watcher sees change. Before a turn the runner
    has the executor client synchronize them (`client.py catch-up`, named in the
    session's execution target) and, when they changed, the session reloads its
    skills before the turn starts."""
    # The executor client, standing in: the project gained a skill, which it
    # links into the config dir, and it says so; after that nothing changes.
    client = machine.root / "client.py"
    client.write_text(
        "import json, pathlib, sys\n"
        f"added = pathlib.Path({str(machine.config / 'skills/pulled')!r})\n"
        "assert sys.argv[1:3] == ['catch-up', sys.argv[2]]\n"
        "changed = not added.exists()\n"
        "if changed:\n"
        "    added.mkdir(parents=True)\n"
        "    (added / 'SKILL.md').write_text('---\\nname: pulled\\n"
        "description: Came with a pull. PULLED_LISTED.\\n---\\nPULLED_BODY\\n')\n"
        "with open(sys.argv[2] + '.calls', 'a') as calls:\n"
        "    calls.write('called\\n')\n"
        "print(json.dumps({'changed': changed}))\n"
    )
    execution = machine.root / "execution.json"
    execution.write_text(json.dumps({"helper": [sys.executable, str(client)]}))

    screen = Screen(machine, env={"CHEESE_EXECUTION_CONFIG": str(execution)})
    try:
        requests = machine.server.state["requests"]
        work = str(uuid.uuid4())
        screen.send("hello", work_id=work)
        first = screen.wait(_ends(work))
        asked = len(requests)
        offered = "PULLED_LISTED" in json.dumps(requests[-1])
        work = str(uuid.uuid4())
        screen.send(contract.do("Skill", skill="pulled"), work_id=work)
        screen.wait(_ends(work), after=first["sequence"])
        loaded = "PULLED_BODY" in json.dumps(requests[asked:])
    finally:
        screen.stop()

    calls = machine.root / "execution.json.calls"
    assert calls.exists(), screen.log()
    assert calls.read_text().count("called") == 2, "once before each turn"
    assert offered, "the first turn was not offered the skill the project gained"
    assert loaded
