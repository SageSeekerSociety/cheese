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
from app.domain.agent.harness.driven import runner as driven
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.driven.runner import (
    IDLE_EXIT_S,
    reply_owed,
    reply_owed_path,
    socket_path,
)
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

    #: The epoch the platform registers this runner under (``lifecycle``).
    EPOCH = 1

    def call(self, method, params=None, timeout=60.0, *, epoch=True):
        params = dict(params or {})
        if epoch and method not in ("ping", "register", "reply_check"):
            # Everything but a greeting is the platform's, and carries the
            # registration it holds; registered on the first such call.
            if not getattr(self, "registered", False):
                self.registered = True
                self.call("register", {"epoch": self.EPOCH})
            params.setdefault("epoch", self.EPOCH)
        answer = self.raw(method, params, timeout)
        assert "error" not in answer, answer
        return answer["result"]

    def raw(self, method, params, timeout=60.0) -> dict:
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
                json.dumps({"method": method, "params": params}).encode() + b"\n"
            )
            return json.loads(client.makefile("rb").readline())

    def records(self) -> list[dict]:
        """The whole journal, read the way the platform reads it."""
        found, after = [], 0
        while True:
            page = self.call("read", {"records_after": after})["records"]
            found += page
            if len(page) < PAGE:
                return found
            after = page[-1]["sequence"]

    def events(self) -> list[dict]:
        """Every event the runner reported so far, unacknowledged: a read
        that acknowledges nothing hands all of them over again."""
        return self.call("read", {"records_after": 0})["events"]

    def wait_event(self, predicate, timeout=90.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            for event in self.events():
                if predicate(event):
                    return event
            assert time.monotonic() < deadline, json.dumps(self.events())[-3000:]
            time.sleep(0.2)

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


def _input(identifier, status):
    return lambda e: (
        e["kind"] == "input" and e["input_id"] == identifier and e["status"] == status
    )


def _status(worker_status, work=None):
    return lambda e: (
        e["kind"] == "status"
        and e["worker_status"] == worker_status
        and (work is None or e["work_id"] == work)
    )


def test_a_send_is_written_read_and_its_work_goes_idle_in_order(screen, contract):
    """What a send becomes, as the runner reports it: the input written, the
    session running on its work, the input read (its echo), and the session
    idle again with how the work ended and what it read — in that order, under
    one epoch, at increasing sequences."""
    work = str(uuid.uuid4())
    sent = screen.send("hello", work_id=work)
    idle = screen.wait_event(_status("idle", work))
    events = screen.events()
    session = screen.call("ping")["session_id"]

    written = next(e for e in events if _input(sent, "written")(e))
    running = next(e for e in events if _status("running", work)(e))
    read = next(e for e in events if _input(sent, "read")(e))
    assert written["seq"] < running["seq"] < read["seq"] < idle["seq"]
    assert {e["epoch"] for e in events} == {Screen.EPOCH}
    assert read["session_id"] == session
    assert read["work_id"] == work and read["execution_work_id"] == work
    assert running["unsolicited"] is False
    assert idle["result"]["failure"] is None
    assert idle["result"]["inputs"] == [sent]
    assert idle["result"]["text"]
    assert idle["result"]["usage"]["output_tokens"] >= 0
    assert screen.call("ping")["working"] is False
    # The journal still carries what the session wrote, stamped with its work.
    echo = next(e for e in screen.records() if _echo_of(sent)(e["record"]))
    assert echo["record"]["cheese"]["work_id"] == work


def test_a_platform_call_is_told_the_run_by_the_event_that_began_it(screen):
    """The session's helper asks which run its platform call is made in, and is
    told the run by the same name the platform applies the ``running`` event
    under: its epoch and seq. Idle afterwards (a background task's call), it is
    still that run; before the session has run, there is no run to name."""
    assert screen.call("run") == {}
    work = str(uuid.uuid4())
    screen.send("hello", work_id=work)
    screen.wait_event(_status("idle", work))
    running = next(e for e in screen.events() if _status("running", work)(e))

    assert screen.call("run") == {
        "run": f"claude-code/{running['epoch']}/{running['seq']}"
    }


def test_an_acknowledged_event_is_not_handed_over_again(screen):
    """The runner keeps an event until the platform acknowledges it, and only
    that long: a read that acknowledges nothing gets it again, one that
    acknowledges it does not."""
    work = str(uuid.uuid4())
    screen.send("hello", work_id=work)
    idle = screen.wait_event(_status("idle", work))

    assert idle in screen.events()
    left = screen.call("read", {"ack": idle["seq"]})["events"]
    assert all(e["seq"] > idle["seq"] for e in left)
    assert idle not in screen.events()


def test_a_call_under_another_epoch_is_refused(screen):
    """Only the registration the runner holds now may ask it anything: one it
    was registered under before, or one it never was, is refused — and so is
    going back to an older one."""
    screen.call("ping")
    screen.call("register", {"epoch": 2})
    screen.registered = True
    stale = screen.raw("read", {"epoch": 1})
    assert "stale worker epoch" in stale["error"]
    assert "stale worker epoch" in screen.raw("register", {"epoch": 1})["error"]
    assert screen.call("read", {"epoch": 2})["worker_epoch"] == 2


def test_a_steer_is_read_only_at_the_tool_boundary(screen, contract):
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
    screen.wait_event(_status("idle", work))

    # Read inside the work it was said to, once the running tool returned.
    assert boundary["sequence"] < echo["sequence"]
    events = screen.events()
    read = next(e for e in events if _input(steer, "read")(e))
    assert read["execution_work_id"] == work
    assert [e["work_id"] for e in events if _status("running")(e)] == [work]
    (idle,) = [e for e in events if _status("idle")(e)]
    assert steer in idle["result"]["inputs"]


# --- 2. work the session starts itself ---------------------------------------


def test_work_the_session_starts_itself_is_unsolicited_with_its_own_work(
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
    screen.wait_event(_status("idle", work))
    own = screen.wait_event(lambda e: _status("running")(e) and e["work_id"] != work)
    screen.wait_event(_status("idle", own["work_id"]))

    assert own["unsolicited"] is True
    uuid.UUID(own["work_id"])


# --- 3. a platform command is nobody's work --------------------------------------


def test_a_platform_command_is_no_rooms_work(screen):
    work = str(uuid.uuid4())
    screen.send("hello", work_id=work)
    screen.wait_event(_status("idle", work))

    answer = screen.call("command", {"text": "/reload-skills"}, timeout=150)

    assert answer["is_error"] is False
    commands = [
        e for e in screen.events() if _status("running")(e) and e["work_id"] != work
    ]
    assert [e["work_id"] for e in commands] == [None]
    assert not any(
        e["kind"] == "input" and e["input_id"].startswith("cheese-command-")
        for e in screen.events()
    )


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


def test_an_interrupt_is_the_failure_of_the_work_it_ends(screen, contract):
    work = str(uuid.uuid4())
    sent = screen.send(
        contract.do("Bash", command="sleep 60; echo LATE", description="long"),
        work_id=work,
    )
    screen.wait(_is("system", "task_started"))

    assert screen.call("interrupt") == {"interrupted": True}

    idle = screen.wait_event(_status("idle", work))
    assert idle["result"]["failure"] == {"kind": "interrupted"}
    assert idle["result"]["inputs"] == [sent]
    assert screen.call("ping")["working"] is False


def test_an_api_failure_is_typed_and_names_what_the_work_read(contract, tmp_path):
    """Work the API killed ends there, and the runner says so on the spot,
    typed: the kind of failure, the status, what the build called it, and the
    inputs the work had read — a task it left running does not hold that back,
    because that task goes on in a work of its own."""

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

            idle = screen.wait_event(_status("idle", work), timeout=180)
            failure = idle["result"]["failure"]
            assert failure["kind"] == "api_error"
            assert failure["status"] == 429
            assert "Budget has been exceeded" in failure["detail"]
            assert idle["result"]["inputs"] == [sent]
            # The task goes on running in a work of its own.
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
            await runner.dispatch("read", {"epoch": 1, "records_after": 0})
            await asyncio.sleep(0.1)

    runner.outbox.register(1)
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
@pytest.mark.parametrize("previous", ["idle", "running", "restarted"])
async def test_a_queued_input_is_not_filed_under_the_previous_work(
    tmp_path, monkeypatch, previous
):
    """The next input can queue long after the last result, or while another
    work is running. Its queue notice names its delivery, but queueing alone
    does not make it part of any execution work."""
    state = tmp_path / "state"
    runner = Runner(state)
    runner.session_id = "session-queue"
    runner.outbox.register(1)

    async def written(message):
        pass

    monkeypatch.setattr(runner, "_write", written)
    old_input, old_work = str(uuid.uuid4()), str(uuid.uuid4())
    await runner.send(old_input, "first", work_id=old_work)
    runner.observe(
        {"type": "command_lifecycle", "command_uuid": old_input, "state": "started"}
    )
    if previous != "running":
        runner.observe({"type": "result", "result": "done"})
    next_input, next_work = str(uuid.uuid4()), str(uuid.uuid4())
    await runner.send(next_input, "next", work_id=next_work)
    if previous == "restarted":
        runner = Runner(state)
        runner.session_id = "session-queue"
        runner.outbox.register(1)
        monkeypatch.setattr(runner, "_write", written)

    runner.observe(
        {"type": "command_lifecycle", "command_uuid": next_input, "state": "queued"}
    )
    queued = runner.journal.read(0)[-1]["record"]
    assert queued["cheese"].get("work_id") is None
    assert queued["cheese"]["input_work_id"] == next_work
    assert runner.working is (previous == "running")
    assert runner.work == (old_work if previous == "running" else None)

    runner.observe(
        {"type": "command_lifecycle", "command_uuid": next_input, "state": "started"}
    )
    started = runner.journal.read(0)[-1]["record"]
    execution = old_work if previous == "running" else next_work
    assert started["cheese"]["work_id"] == execution
    assert started["cheese"]["input_work_id"] == next_work
    assert runner.working and runner.work == execution


@pytest.mark.anyio
async def test_a_turn_aborted_before_its_prompt_was_checked_still_lets_the_session_go(
    tmp_path, monkeypatch
):
    """The build can abort a turn before its prompt is checked: the input is
    never echoed back, and the build says it was cancelled. Nothing is on its
    way in after that, so the session goes once it has sat idle, and the
    person's message no longer holds back the session's tools."""
    clock = [1000.0]

    class Clock:
        @staticmethod
        def monotonic():
            return clock[0]

    monkeypatch.setattr(driven, "time", Clock)
    monkeypatch.setattr(driven, "IDLE_CHECK_S", 0.01)
    runner = Runner(tmp_path / "state")
    runner.session_id = "session-aborted"
    released = asyncio.Event()

    async def release():
        released.set()

    async def written(message):
        pass

    monkeypatch.setattr(runner, "release", release)
    monkeypatch.setattr(runner, "_write", written)
    await runner.listen(2**16)
    runner.outbox.register(1)
    try:
        identifier = str(uuid.uuid4())
        await runner.dispatch(
            "send",
            {
                "epoch": 1,
                "input_id": identifier,
                "work_id": identifier,
                "text": "hello",
                "owes_reply": True,
            },
        )
        for record in (
            {
                "type": "command_lifecycle",
                "command_uuid": identifier,
                "state": "started",
            },
            {
                "type": "result",
                "subtype": "error_during_execution",
                "is_error": True,
                "terminal_reason": "aborted_streaming",
                "errors": [
                    "prompt_check: the turn was stopped before its prompt was checked"
                ],
            },
            {
                "type": "command_lifecycle",
                "command_uuid": identifier,
                "state": "cancelled",
            },
        ):
            runner.observe(
                {**record, "session_id": runner.session_id, "uuid": str(uuid.uuid4())}
            )
        await runner.dispatch("read", {"epoch": 1, "records_after": 0})
        clock[0] += IDLE_EXIT_S + 1
        await asyncio.wait_for(released.wait(), 5)
        assert reply_owed(reply_owed_path(runner.state)) is None
    finally:
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
        {
            "type": "stream_event",
            "ttft_ms": 1234,
            "event": {"type": "message_start", "message": {"id": "msg_1"}},
        },
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
    runner.outbox.register(1)

    async def shown(seen: str | None) -> dict:
        answer = await runner.dispatch(
            "read",
            {
                "epoch": 1,
                "ack": runner.outbox.last(),
                "records_after": runner.journal.last(),
                "wait": 10,
                "live": seen,
            },
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


@pytest.mark.anyio
async def test_how_long_a_call_waited_to_start_is_kept_with_what_it_wrote(
    monkeypatch, tmp_path
):
    """The wait before the first token is what a person sits through before
    any of the answer can be shown. The build reports it once per API call, on
    the `message_start` of the stream it prints; the deltas are shown and
    never journaled, so the number is carried on the record the call writes —
    the finished block that shares its message id."""
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
    try:
        async with asyncio.timeout(30):
            for _ in range(len(_WRITING)):
                stdin.write(b"\n")
                await stdin.drain()
            while True:
                records = [e["record"] for e in runner.journal.read(0)]
                if any(_is("result")(r) for r in records):
                    break
                await asyncio.sleep(0.05)
    finally:
        await runner.close()

    waits = [
        r["cheese"]["first_token_ms"]
        for r in records
        if r.get("type") == "assistant" and "first_token_ms" in r.get("cheese", {})
    ]
    assert waits == [1234], "the call's wait is not on the record it wrote"


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


# --- N. the session compacting itself -------------------------------------------


def test_an_idle_session_compacts_itself_when_asked(screen, contract):
    """`cheese_compact` reaches this runner as `compact`, and the runner writes
    `/compact` — the same door `/reload-skills` goes through. An idle session
    has no turn to wait for, so it goes at once, and the build reports the
    compaction the way the room already reads it (`system/status`)."""
    work = str(uuid.uuid4())
    screen.send("hello", work_id=work)
    screen.wait_event(_status("idle", work))

    answer = screen.call("compact", {})

    assert answer == {"requested": True, "already": False}
    started = screen.wait(lambda r: r.get("status") == "compacting")
    ended = screen.wait(
        lambda r: r.get("compact_result") is not None, after=started["sequence"]
    )
    assert ended["record"]["compact_result"] == "success"


def test_a_request_made_mid_turn_is_written_when_that_turn_ends(screen, contract):
    """A tool call is answered at a tool boundary, and the build takes stdin at
    those boundaries: `/compact` written from inside one would arrive inside the
    turn that asked. So it waits for that turn's ending — one per request, and
    the second asking in the same turn is told it is already asked."""
    work = str(uuid.uuid4())
    screen.send(
        contract.do("Bash", command="sleep 4; echo HELD", description="hold the turn"),
        work_id=work,
    )
    # The Bash is really running: asked for a tool call, the build is inside
    # one, and the turn cannot end before its result comes back.
    screen.wait(_is("system", "task_started"))
    assert screen.call("ping")["work_id"] == work

    first = screen.call("compact", {})
    second = screen.call("compact", {})

    assert first == {"requested": True, "already": False}
    assert second == {"already": True}
    held = screen.records()
    assert not any(
        block.get("text") == "/compact"
        for entry in held
        for block in _blocks(entry["record"])
    ), "the command went out inside the turn that asked"
    screen.wait_event(_status("idle", work))
    screen.wait(lambda r: r.get("status") == "compacting")

    # Once per turn, not once for ever: the session's next turn can ask again,
    # and this time nothing is running, so the command goes at once.
    second_work = str(uuid.uuid4())
    screen.send("again", work_id=second_work)
    screen.wait_event(_status("idle", second_work))
    assert screen.call("compact", {}) == {"requested": True, "already": False}
    started = screen.wait(
        lambda r: r.get("status") == "compacting", after=held[-1]["sequence"]
    )
    ended = screen.wait(
        lambda r: r.get("compact_result") is not None, after=started["sequence"]
    )
    assert ended["record"]["compact_result"] == "success"
