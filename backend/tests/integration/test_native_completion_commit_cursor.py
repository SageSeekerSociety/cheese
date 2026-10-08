"""Native completion survives commit failure, old output and backend replacement.

The pinned Claude build uses a local deterministic Messages API. Recovery creates
only a settlement reader in a new Python interpreter, not a full executor.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import ValidationError
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.live_work import LiveWork
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.input_identity import InputEffects
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_native_echo_commit_cursor import _RECOVER
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room
from tests.unit.test_claude_runner import Machine

_RECOVER_COMPLETION = _RECOVER.replace(
    "announce=noop, receipts=chat.confirm_prompt_receipt)",
    "announce=noop, receipts=chat.confirm_prompt_receipt, "
    "completions=chat.confirm_work_completion)",
)


@pytest.mark.parametrize("old", [False, True], ids=["poison", "older-than-retention"])
def test_native_completion_commit_failure_replays_after_start_landed(
    client, tmp_path, old
):
    scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
    sys.path.insert(0, scripts)
    import headless_contract

    machine = Machine(headless_contract, tmp_path)
    project = uuid.UUID(_project(client, "completion cursor recovery"))
    topic = uuid.UUID(_room(client, str(project), "completion"))

    async def run():
        factory = client.test_request_factory
        identity = replace(_identity(project, topic), harness=CLAUDE_CODE)
        ids = await _blocks(factory, project, topic)
        chat = ChatService.__new__(ChatService)
        chat._sessions = factory
        chat.live = LiveWork()
        runner = Runner(machine.state)
        identity = replace(
            identity,
            native_session_id=await runner.start(
                command=machine.command,
                env=machine.env,
                resume=None,
                agent_handle=identity.recipient_handle,
            ),
        )
        await chat._input_registrar(InputEffects(held_block_ids=ids))(identity)
        await runner.send(
            str(identity.input_id), "complete this work", work_id=str(identity.work_id)
        )
        async with asyncio.timeout(90):
            while not any(
                row["record"].get("type") == "result" for row in runner.journal.read()
            ):
                await asyncio.sleep(0.05)
        records = runner.journal.read()
        result = next(row for row in records if row["record"].get("type") == "result")
        result_seq = result["sequence"]
        assert result["record"]["cheese"]["work_completed"] is True
        assert result["record"]["cheese"]["work_id"] == str(identity.work_id)
        assert (
            result["record"]["cheese"]["completion_session_id"]
            == identity.native_session_id
        )
        assert runner.working is False and runner.work is None
        # Only the process created by this fixture is closed; no shared service.
        await runner.close()
        runner_path = runner.state / "records.sqlite"
        journal = Journal(runner_path)
        try:
            last_sequence = journal.read()[-1]["sequence"]
        finally:
            journal.close()
        mirror_path = tmp_path / "mirror.sqlite"
        ref = SessionRef(project, topic, identity.recipient_handle, harness=CLAUDE_CODE)
        include_result = False
        calls = []

        async def remote(method, params):
            calls.append(method)
            assert method == "events", "Reconciliation must not call send/steer"
            journal = Journal(runner_path)
            try:
                page = journal.read(params["after"])
                return {
                    "events": [
                        row
                        for row in page
                        if include_result or row["sequence"] < result_seq
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
            session_id=identity.native_session_id,
            recipient_handle=identity.recipient_handle,
            announce=noop,
            receipts=chat.confirm_prompt_receipt,
            completions=chat.confirm_work_completion,
        )
        try:
            await reading.drain()
            journal = Journal(mirror_path)
            try:
                assert journal.recall("landed") == str(result_seq - 1)
            finally:
                journal.close()
            async with factory() as session:
                row = await session.scalar(
                    select(NativeInput).where(NativeInput.input_id == identity.input_id)
                )
                assert row.echoed_at is not None and row.released_block_ids == []
                url = session.bind.url.render_as_string(hide_password=False)
            include_result = True
            await reading.receive()
            if old:
                journal = Journal(runner_path)
                try:
                    before = (datetime.now(UTC) - timedelta(days=3)).isoformat()
                    with journal.connection:
                        journal.connection.execute(
                            "UPDATE records SET recorded_at=? WHERE sequence=?",
                            (before, result_seq),
                        )
                    journal.expire((datetime.now(UTC) - timedelta(days=1)).isoformat())
                    assert any(row["sequence"] == result_seq for row in journal.read())
                finally:
                    journal.close()
                journal = Journal(mirror_path)
                try:
                    with journal.connection:
                        journal.connection.execute(
                            "UPDATE records SET recorded_at=? WHERE sequence=?",
                            (before, result_seq),
                        )
                finally:
                    journal.close()
            else:
                # A conflicting durable work must neither release nor land.
                journal = Journal(mirror_path)
                original = result["record"]
                conflicting = {
                    **original,
                    "cheese": {**original["cheese"], "work_id": str(uuid.uuid4())},
                }
                try:
                    with journal.connection:
                        journal.connection.execute(
                            "UPDATE records SET record=? WHERE sequence=?",
                            (json.dumps(conflicting), result_seq),
                        )
                finally:
                    journal.close()
                with pytest.raises(ValidationError):
                    await reading.drain()
                journal = Journal(mirror_path)
                try:
                    assert journal.recall("landed") == str(result_seq - 1)
                    with journal.connection:
                        journal.connection.execute(
                            "UPDATE records SET record=? WHERE sequence=?",
                            (json.dumps(original), result_seq),
                        )
                finally:
                    journal.close()
            journal = Journal(mirror_path)
            try:
                journal.remember(
                    "refused",
                    json.dumps(
                        {
                            "key": str(result_seq),
                            "times": 3,
                            "since": (
                                datetime.now(UTC) - timedelta(minutes=11)
                            ).isoformat(),
                        }
                    ),
                )
            finally:
                journal.close()
            aborted = []
            # The listener is on every Session in the process, and the app's
            # periodic jobs commit too: only this task's commits are the drain's.
            draining = asyncio.current_task()

            def fail_commit(session):
                if asyncio.current_task() is not draining:
                    return
                session.flush()
                aborted.append(True)
                session.execute(text("SELECT 1 / 0"))

            event.listen(Session, "before_commit", fail_commit)
            try:
                for _ in range(4):
                    refused = False
                    try:
                        await reading.drain()
                    except DBAPIError:
                        refused = True
                    journal = Journal(mirror_path)
                    try:
                        assert journal.recall("landed") == str(result_seq - 1)
                        journal.prune(
                            (datetime.now(UTC) + timedelta(days=1)).isoformat()
                        )
                        assert any(
                            row["sequence"] == result_seq for row in journal.read()
                        )
                    finally:
                        journal.close()
                    assert refused, "The real completion commit must fail"
            finally:
                event.remove(Session, "before_commit", fail_commit)
            assert aborted == [True] * 4
        finally:
            await reading.release()
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert row.released_block_ids == []
            blocks = list(await session.scalars(select(Block).where(Block.id.in_(ids))))
            assert all(consumed_turn(block) is None for block in blocks)
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _RECOVER_COMPLETION,
            url,
            str(runner_path),
            str(mirror_path),
            str(project),
            str(topic),
            identity.recipient_handle,
            identity.harness,
            identity.native_session_id,
            env={**os.environ, "PYTHONPATH": "."},
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await child.communicate()
        assert child.returncode == 0, stderr.decode()
        assert json.loads(stdout)["landed"] == str(last_sequence)
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert set(row.released_block_ids) == {str(block) for block in ids}
            assert (
                await held_blocks(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=identity.recipient_handle,
                )
                == set()
            )
            blocks = list(await session.scalars(select(Block).where(Block.id.in_(ids))))
            assert all(
                consumed_turn(block) == str(identity.work_id) for block in blocks
            )
        assert calls and set(calls) == {"events"}

    try:
        client.portal.call(run)
    finally:
        machine.close()
        sys.path.remove(scripts)
