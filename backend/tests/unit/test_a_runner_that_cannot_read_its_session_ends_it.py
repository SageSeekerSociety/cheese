"""A runner that can no longer read its session ends it.

The runner reads the session's stdout one line at a time, and a line it cannot
read (one past ``LINE_LIMIT`` is the known way) used to end only the reading:
the task doing it died with its exception kept on it, while the Claude Code
process went on. The runner kept answering that the session was alive, kept
taking inputs and reporting them written, and never reported one read, the
work going idle, or the process exiting. With a tool call open when it died,
none of its stuck-work clocks ran either, so the seat waited on those inputs
for good.

The session must end instead, the way any session that exits mid-work does:
what it had not read is reported dropped (the platform offers it to the next
session), the work it was doing ends with a failure that says why, and the
process's exit is reported. Why is printed to the runner's log. That holds for
a session that goes on writing after the line nobody can read, and that will
not go when asked; and for one that, as it is ended, starts a process holding
its output open: what stays in its process group is ended with it, and its exit
is reported either way.

A stand-in plays the build: the line it cannot be read past is the whole point,
and the pinned binary does not write one on demand.
"""

import asyncio
import os
import shlex
import signal
import sys
import uuid

import pytest

from app.domain.agent.harness.claude_code.runner import LINE_LIMIT, Runner
from app.domain.agent.harness.driven import lifecycle

pytestmark = pytest.mark.anyio

SAID = [
    {"type": "system", "subtype": "init", "session_id": "s", "model": "m"},
    {
        "type": "assistant",
        "uuid": "a",
        "parent_tool_use_id": None,
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_1",
                    "name": "Bash",
                    "input": {"command": "sleep 600"},
                }
            ],
        },
    },
]

# Starts work with a tool call open, takes the next input off stdin without
# ever echoing it, writes one line too long to read, and keeps running.
BUILD = f"""
import json, sys, time
for record in {SAID!r}:
    print(json.dumps(record), flush=True)
sys.stdin.readline()
sys.stdout.write("x" * {LINE_LIMIT + 1} + "\\n")
sys.stdout.flush()
time.sleep(120)
"""

# The same, then it ignores SIGTERM and goes on writing well past what an
# unread pipe holds.
CHATTY_BUILD = BUILD.replace(
    "time.sleep(120)",
    f"""import signal
signal.signal(signal.SIGTERM, signal.SIG_IGN)
for _ in range(4):
    sys.stdout.write("y" * {LINE_LIMIT} + "\\n")
    sys.stdout.flush()
time.sleep(120)""",
)


@pytest.mark.parametrize(
    ("build_source", "within"),
    [(BUILD, 8), (CHATTY_BUILD, 20)],
    ids=["goes-quiet", "keeps-writing-and-ignores-sigterm"],
)
async def test_a_session_its_runner_cannot_read_is_ended_and_its_inputs_dropped(
    tmp_path, monkeypatch, capsys, build_source, within
):
    build = tmp_path / "build.py"
    build.write_text(build_source)
    monkeypatch.chdir(tmp_path)
    runner = Runner(tmp_path / "state")
    runner.outbox.register(1)
    await runner.start(
        command=shlex.join([sys.executable, str(build)]),
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
        resume=None,
        agent_handle="cheese-agent",
    )
    message = str(uuid.uuid4())
    try:
        async with asyncio.timeout(5):
            while not runner.working:
                await asyncio.sleep(0.02)
        await runner.send(
            message, "are you still there?", work_id=str(uuid.uuid4()), steering=True
        )

        async with asyncio.timeout(within):
            while (await runner.dispatch("ping", {}))["alive"]:
                await asyncio.sleep(0.05)
            events: list[dict] = []
            while not any(e["kind"] == lifecycle.EXITED for e in events):
                answer = await runner.dispatch("read", {"epoch": 1, "ack": 0})
                events = answer["events"]
                await asyncio.sleep(0.05)
    finally:
        await runner.close()

    about_message = [e for e in events if e.get("input_id") == message]
    assert [e["status"] for e in about_message] == [
        lifecycle.WRITTEN,
        lifecycle.DROPPED,
    ]
    (idle,) = [e for e in events if e.get("worker_status") == lifecycle.IDLE]
    assert idle["result"]["failure"]["kind"] == lifecycle.READER_FAILED
    assert events[-1]["kind"] == lifecycle.EXITED
    log = capsys.readouterr().err
    assert "can no longer be read" in log
    assert "Traceback" in log


# Answers SIGTERM by starting a process that inherits its stdout, then exits.
# That process either stays in the session's process group or leaves it.
LATE_CHILD_BUILD = BUILD.replace(
    "time.sleep(120)",
    """import signal, subprocess
def leave(*_):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(120)"],
        start_new_session=LEAVES_GROUP,
    )
    open(CHILD_PID, "w").write(str(child.pid))
    sys.exit(0)
signal.signal(signal.SIGTERM, leave)
time.sleep(120)""",
)


def _gone(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM handlers are POSIX")
@pytest.mark.parametrize("leaves_group", [False, True], ids=["in-group", "own-group"])
async def test_a_session_ending_is_reported_though_what_it_started_holds_its_output(
    tmp_path, monkeypatch, leaves_group
):
    child_pid = tmp_path / "child.pid"
    build = tmp_path / "build.py"
    build.write_text(
        f"LEAVES_GROUP = {leaves_group!r}\nCHILD_PID = {str(child_pid)!r}\n"
        + LATE_CHILD_BUILD
    )
    monkeypatch.chdir(tmp_path)
    runner = Runner(tmp_path / "state")
    runner.outbox.register(1)
    await runner.start(
        command=shlex.join([sys.executable, str(build)]),
        env={**os.environ, "CLAUDE_CONFIG_DIR": str(tmp_path / "config")},
        resume=None,
        agent_handle="cheese-agent",
    )
    message = str(uuid.uuid4())
    try:
        async with asyncio.timeout(5):
            while not runner.working:
                await asyncio.sleep(0.02)
        await runner.send(
            message, "are you still there?", work_id=str(uuid.uuid4()), steering=True
        )
        events: list[dict] = []
        async with asyncio.timeout(15):
            while not any(e["kind"] == lifecycle.EXITED for e in events):
                answer = await runner.dispatch("read", {"epoch": 1, "ack": 0})
                events = answer["events"]
                await asyncio.sleep(0.05)
        about_message = [e for e in events if e.get("input_id") == message]
        assert [e["status"] for e in about_message] == [
            lifecycle.WRITTEN,
            lifecycle.DROPPED,
        ]
        if not leaves_group:
            # What it started while being ended was ended with it.
            async with asyncio.timeout(3):
                while not _gone(int(child_pid.read_text())):
                    await asyncio.sleep(0.05)
    finally:
        if child_pid.exists() and not _gone(pid := int(child_pid.read_text())):
            os.kill(pid, signal.SIGKILL)
        await asyncio.wait_for(runner.close(), 15)
