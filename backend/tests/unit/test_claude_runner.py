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
from app.domain.agent.harness.claude_code.runner import Runner
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

    def __init__(self, contract, root: Path):
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
        self.server = contract.Server(("127.0.0.1", 0), contract.Handler)
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

    def __init__(self, machine: Machine):
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
    assert echo["record"]["cheese"] == {**owner, "receipt": True}
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


# --- 6-8: the class itself, where the archive exposes no knob --------------------


async def _started(machine, monkeypatch, **options) -> Runner:
    # The build runs where it was started: the isolated workspace, not the repo.
    monkeypatch.chdir(machine.workspace)
    runner = Runner(machine.state, **options)
    await runner.start(
        command=machine.command, env=machine.env, resume=None, agent_handle=AGENT
    )
    return runner


@pytest.mark.anyio
async def test_a_session_already_on_this_config_dir_is_ended_first(
    machine, monkeypatch, tmp_path
):
    """Two sessions appending to one transcript corrupt it."""
    stand_in = tmp_path / "bin/claude"
    stand_in.parent.mkdir()
    # A symlink keeps Python's libraries reachable under the process name.
    stand_in.symlink_to(sys.executable)
    sleep = [str(stand_in), "-c", "import time; time.sleep(120)"]
    same = subprocess.Popen(
        sleep, env={**os.environ, "CLAUDE_CONFIG_DIR": str(machine.config)}
    )
    other = subprocess.Popen(
        sleep, env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "elsewhere")}
    )
    try:
        runner = await _started(machine, monkeypatch)
        try:
            assert same.wait(timeout=10) is not None
            assert other.poll() is None
        finally:
            await runner.close()
    finally:
        for process in (same, other):
            process.kill()
            process.wait()


@pytest.mark.anyio
async def test_an_idle_unread_session_is_let_go_by_closing_its_stdin(
    machine, monkeypatch
):
    runner = await _started(machine, monkeypatch, idle_exit_s=0.1)
    try:
        assert runner.process is not None
        # Closing stdin is the build's own way out: a clean exit, not a kill.
        assert await asyncio.wait_for(runner.process.wait(), 60) == 0
    finally:
        await runner.close()


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
        "工作机器仍在准备，对话和平台工具仍可用"
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
