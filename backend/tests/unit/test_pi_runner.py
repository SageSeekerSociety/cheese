"""The runner as the backend reaches it: over a socket, across a reconnection.

Driven against a real subprocess speaking pi's protocol rather than a fake
object, because what is being tested is the process, its pipes and its framing.
"""

import asyncio
import json
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
