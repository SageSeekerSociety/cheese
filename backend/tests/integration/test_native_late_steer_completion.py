"""A native steer read after its addressed work ends belongs to its new work.

The input is durably registered while W is working. Its external write is held
until W's result, reproducing the runtime-to-runner race without timing guesses.
Pinned Claude uses the deterministic local Messages API, not a live provider.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from pathlib import Path

from sqlalchemy import select

from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.input_identity import InputEffects
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_native_completion_commit_cursor import _RECOVER_COMPLETION
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room
from tests.unit.test_claude_runner import Machine


def test_late_steer_finishes_only_under_its_echoed_native_execution_owner(
    client, tmp_path
):
    scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
    sys.path.insert(0, scripts)
    import headless_contract

    machine = Machine(headless_contract, tmp_path)
    project = uuid.UUID(_project(client, "late steer execution owner"))
    topic = uuid.UUID(_room(client, str(project), "late steer"))

    async def run():
        factory = client.test_request_factory
        chat = ChatService.__new__(ChatService)
        chat._sessions, chat._unread_inputs = factory, {}
        runner = Runner(machine.state)
        initial = replace(_identity(project, topic), harness=CLAUDE_CODE)
        initial = replace(
            initial,
            native_session_id=await runner.start(
                command=machine.command,
                env=machine.env,
                resume=None,
                agent_handle=initial.recipient_handle,
            ),
        )
        first_ids = await _blocks(factory, project, topic)
        late_ids = await _blocks(factory, project, topic)
        late = replace(initial, input_id=uuid.uuid4())
        await chat._input_registrar(InputEffects(held_block_ids=first_ids))(initial)
        await runner.send(
            str(initial.input_id),
            headless_contract.do(
                "Bash", command="sleep 2; printf INITIAL_DONE", description="wait"
            ),
            work_id=str(initial.work_id),
        )
        async with asyncio.timeout(90):
            while not runner.working:
                await asyncio.sleep(0.01)
        assert runner.work == str(initial.work_id)
        # Runtime admission sees W. The runner may finish W before this stdin write.
        await chat._input_registrar(InputEffects(held_block_ids=late_ids))(late)
        async with asyncio.timeout(90):
            while not any(
                row["record"].get("type") == "result" for row in runner.journal.read()
            ):
                await asyncio.sleep(0.05)
        first_result = next(
            row
            for row in runner.journal.read()
            if row["record"].get("type") == "result"
        )
        assert first_result["record"]["cheese"]["completion_input_ids"] == [
            str(initial.input_id)
        ]
        assert not runner.working
        await runner.send(
            str(late.input_id),
            "LATE_STEER",
            work_id=str(initial.work_id),
            steering=True,
        )
        async with asyncio.timeout(90):
            while (
                len(
                    [
                        row
                        for row in runner.journal.read()
                        if row["record"].get("type") == "result"
                    ]
                )
                < 2
            ):
                await asyncio.sleep(0.05)
        rows = runner.journal.read()
        echo = next(
            row
            for row in rows
            if row["record"].get("uuid") == str(late.input_id)
            and row["record"].get("isReplay")
        )
        final = [row for row in rows if row["record"].get("type") == "result"][-1]
        execution = uuid.UUID(echo["record"]["cheese"]["receipt_execution_work_id"])
        assert execution != initial.work_id
        assert echo["record"]["cheese"]["receipt_work_id"] == str(initial.work_id)
        assert first_result["sequence"] < echo["sequence"] < final["sequence"]
        assert final["record"]["cheese"]["unsolicited"] is True
        assert final["record"]["cheese"]["work_id"] == str(execution)
        assert final["record"]["cheese"]["completion_input_ids"] == [str(late.input_id)]
        await runner.close()
        runner_path, mirror_path = (
            runner.state / "records.sqlite",
            tmp_path / "mirror.sqlite",
        )
        journal = Journal(runner_path)
        try:
            last = journal.read()[-1]["sequence"]
        finally:
            journal.close()
        ref = SessionRef(project, topic, initial.recipient_handle, harness=CLAUDE_CODE)

        async def remote(method, params):
            assert method == "events", "Completion replay must not call send/steer"
            journal = Journal(runner_path)
            try:
                return {
                    "events": [
                        row
                        for row in journal.read(params["after"])
                        if row["sequence"] <= first_result["sequence"]
                    ]
                }
            finally:
                journal.close()

        async def noop(*args):
            pass

        reading = Subscription(
            ref,
            mirror_path,
            remote,
            noop,
            noop,
            session_id=initial.native_session_id,
            recipient_handle=initial.recipient_handle,
            announce=noop,
            receipts=chat.confirm_prompt_receipt,
            completions=chat.confirm_work_completion,
        )
        try:
            await reading.drain()
        finally:
            await reading.release()
        async with factory() as session:
            held = await held_blocks(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=initial.recipient_handle,
            )
            assert held == set(late_ids)
            a = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == initial.input_id)
            )
            b = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == late.input_id)
            )
            assert set(a.released_block_ids) == {str(block) for block in first_ids}
            assert b.echoed_at is None and b.released_block_ids == []
            url = session.bind.url.render_as_string(hide_password=False)
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _RECOVER_COMPLETION,
            url,
            str(runner_path),
            str(mirror_path),
            str(project),
            str(topic),
            initial.recipient_handle,
            initial.harness,
            initial.native_session_id,
            env={**os.environ, "PYTHONPATH": "."},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await child.communicate()
        assert child.returncode == 0, stderr.decode()
        assert json.loads(stdout)["landed"] == str(last)
        async with factory() as session:
            b = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == late.input_id)
            )
            assert b.work_id == initial.work_id and b.execution_work_id == execution
            assert set(b.released_block_ids) == {str(block) for block in late_ids}
            assert (
                await held_blocks(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=initial.recipient_handle,
                )
                == set()
            )
            blocks = list(
                await session.scalars(select(Block).where(Block.id.in_(late_ids)))
            )
            assert all(consumed_turn(block) == str(execution) for block in blocks)

    try:
        client.portal.call(run)
    finally:
        machine.close()
        sys.path.remove(scripts)
