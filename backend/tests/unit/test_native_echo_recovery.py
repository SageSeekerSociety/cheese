"""A replacement runner correlates echoes by persisted input identity.

These use native-shaped records, not a running Claude binary or model.
The replacement observes in a child interpreter and never writes stdin.
"""

import asyncio
import json
import os
import subprocess
import sys
import uuid

import pytest

from app.domain.agent.harness.claude_code.runner import Runner

_OBSERVE = """
import json, sys
from pathlib import Path
from app.domain.agent.harness.claude_code.runner import Runner
runner = Runner(Path(sys.argv[1]))
runner.session_id = sys.argv[2]
runner.working = True
runner.work = sys.argv[3]
runner.outbox.register(1)
runner.observe(json.loads(sys.argv[4]))
print(json.dumps({"records": runner.journal.read(), "events": runner.outbox.pending()}))
runner.journal.close()
"""


@pytest.mark.parametrize("write_failed", [False, True])
def test_replacement_preserves_input_work_even_when_active_work_changed(
    tmp_path, write_failed
):
    native_session, input_id, input_work, active_work = [
        str(uuid.uuid4()) for _ in range(4)
    ]
    writes = []

    async def prepare():
        runner = Runner(tmp_path)
        runner.session_id = native_session

        async def write(record):
            writes.append(record)
            if write_failed:
                raise BrokenPipeError("stdin drain failed after write")

        runner._write = write
        try:
            if write_failed:
                with pytest.raises(BrokenPipeError):
                    await runner.send(
                        input_id, "the answer", work_id=input_work, steering=True
                    )
            else:
                await runner.send(
                    input_id, "the answer", work_id=input_work, steering=True
                )
        finally:
            runner.journal.close()

    asyncio.run(prepare())
    echo = {**writes[0], "isReplay": True}
    observed = subprocess.run(
        [
            sys.executable,
            "-c",
            _OBSERVE,
            str(tmp_path),
            native_session,
            active_work,
            json.dumps(echo),
        ],
        env={**os.environ, "PYTHONPATH": "."},
        capture_output=True,
        text=True,
        check=True,
    )
    seen = json.loads(observed.stdout)
    (row,) = seen["records"]
    assert row["record"]["cheese"]["work_id"] == active_work
    assert row["record"]["uuid"] == input_id
    # Read under the identity it was written with, into the work running now.
    (read,) = [e for e in seen["events"] if e["status"] == "read"]
    assert read["input_id"] == input_id
    assert read["work_id"] == input_work
    assert read["session_id"] == native_session
    assert read["execution_work_id"] == active_work
    assert len(writes) == 1, "Recovery must not resend the input"


@pytest.mark.parametrize("conflict", ["unknown", "session", "child"])
def test_unrelated_echo_cannot_borrow_a_registered_identity(tmp_path, conflict):
    runner = Runner(tmp_path)
    runner.session_id = "current-session"
    identifier = str(uuid.uuid4())
    runner.journal.remember(
        f"receipt:{identifier}",
        json.dumps(
            {
                "session_id": "other-session"
                if conflict == "session"
                else runner.session_id,
                "work_id": str(uuid.uuid4()),
                "how": "send",
            }
        ),
    )
    runner.outbox.register(1)
    runner.observe(
        {
            "type": "user",
            "uuid": str(uuid.uuid4()) if conflict == "unknown" else identifier,
            "isReplay": True,
            "parent_tool_use_id": "child" if conflict == "child" else None,
            "message": {"role": "user", "content": "same answer"},
        }
    )
    try:
        assert not [
            e
            for e in runner.outbox.pending()
            if e["kind"] == "input" and e["status"] == "read"
        ]
    finally:
        runner.journal.close()


def test_registration_failure_writes_no_stdin(tmp_path):
    runner = Runner(tmp_path)
    runner.session_id = "session"
    writes = []

    async def write(record):
        writes.append(record)

    def refuse_registration(key, value):
        raise OSError("identity registration failed")

    runner._write = write
    runner.journal.remember = refuse_registration
    try:
        with pytest.raises(OSError, match="identity registration failed"):
            asyncio.run(
                runner.send(str(uuid.uuid4()), "answer", work_id=str(uuid.uuid4()))
            )
        assert writes == []
        assert runner.journal.read() == []
    finally:
        runner.journal.close()
