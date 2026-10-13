"""Real PostgreSQL commit abort keeps a runner echo replayable by a new reader.

Cases use either a native-shaped substitute or the pinned Claude binary against
a local deterministic Messages API. Recovery rebuilds the settlement-only
ChatService and mirror/subscription, not a full executor/converse.
"""

import asyncio
import json
import os
import sys
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import get_chat_service
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.runner import Runner
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.block.models import AuthorType, Block, BlockKind, BlockReaction
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.models import Delivery, NativeInput
from app.main import app
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room

_RECOVER = """
import asyncio, json, sys, uuid
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.domain.agent.chat import ChatService
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.turn.state.live import LiveWork
async def run():
    engine = create_async_engine(sys.argv[1])
    chat = ChatService.__new__(ChatService)
    chat._sessions = async_sessionmaker(engine, expire_on_commit=False)
    chat.live = LiveWork()
    ref = SessionRef(uuid.UUID(sys.argv[4]), uuid.UUID(sys.argv[5]),
        sys.argv[6], harness=sys.argv[7])
    async def remote(method, params):
        assert method == 'events', 'Recovery must not call send/steer'
        journal = Journal(Path(sys.argv[2]))
        try:
            return {'events': journal.read(params['after'])}
        finally:
            journal.close()
    async def noop(*args): pass
    reading = Subscription(ref, Path(sys.argv[3]), remote, noop, noop,
        session_id=sys.argv[8], recipient_handle=sys.argv[6],
        announce=noop, receipts=chat.confirm_prompt_receipt,
        completions=chat.confirm_work_completion)
    try:
        await reading.drain()
        await reading.drain()
    finally:
        await reading.release()
        await engine.dispose()
    journal = Journal(Path(sys.argv[3]))
    try:
        print(json.dumps({'landed': journal.recall('landed')}))
    finally:
        journal.close()
asyncio.run(run())
"""


@pytest.mark.parametrize("native", [False, True])
def test_echo_commit_abort_replays_same_identity_in_new_chat_process(
    client, tmp_path, native
):
    machine = None
    if native:
        from tests.unit.test_claude_runner import Machine

        scripts = str(Path(__file__).resolve().parents[3] / "scripts/remote_execution")
        sys.path.insert(0, scripts)
        import headless_contract

        machine = Machine(headless_contract, tmp_path)
    project = uuid.UUID(_project(client, "echo cursor recovery"))
    topic = uuid.UUID(_room(client, str(project), "native echo recovery"))

    async def run():
        chat = app.dependency_overrides[get_chat_service]()
        factory = client.test_request_factory
        identity = InputIdentity(
            project,
            topic,
            "cheese-test",
            CLAUDE_CODE,
            str(uuid.uuid4()),
            uuid.uuid4(),
            uuid.uuid4(),
        )
        runner = Runner(machine.state if machine else tmp_path / "runner")
        if machine:
            identity = replace(
                identity,
                native_session_id=await runner.start(
                    command=machine.command,
                    env=machine.env,
                    resume=None,
                    agent_handle=identity.recipient_handle,
                ),
            )
        else:
            runner.session_id = identity.native_session_id
        delivery_id, attempt_id = uuid.uuid4(), uuid.uuid4()
        block_ids = (uuid.uuid4(), uuid.uuid4())
        async with factory() as session:
            for block_id in block_ids:
                session.add(
                    Block(
                        id=block_id,
                        project_id=project,
                        conversation_id=topic,
                        kind=BlockKind.message,
                        author_type=AuthorType.participant,
                        author="user-1",
                        content="answer",
                        meta={"consumed_turn": None},
                    )
                )
            session.add(
                Delivery(
                    id=delivery_id,
                    event_id=uuid.uuid4(),
                    conversation_id=topic,
                    recipient_handle=identity.recipient_handle,
                    dedup_key=str(uuid.uuid4()),
                    type="mention",
                    payload={},
                    event_at=datetime.now(UTC),
                    recorded_at=datetime.now(UTC),
                    state="sending",
                    attempt_id=attempt_id,
                )
            )
            await session.commit()
            url = session.bind.url.render_as_string(hide_password=False)
        await chat._input_registrar(
            InputEffects(
                held_block_ids=block_ids,
                block_ids=block_ids,
                seen_block_ids=block_ids,
                seen_by=identity.recipient_handle,
                delivery_id=delivery_id,
                attempt_id=attempt_id,
            )
        )(identity)

        writes = []
        original_write = runner._write

        async def write(record):
            # This observation is before the external boundary.
            async with factory() as session:
                row = await session.scalar(
                    select(NativeInput).where(NativeInput.input_id == identity.input_id)
                )
                assert row is not None and row.settled_at is None
            writes.append(record)
            if machine:
                await original_write(record)

        runner._write = write
        await runner.send(
            str(identity.input_id), "answer", work_id=str(identity.work_id)
        )
        if machine:
            async with asyncio.timeout(90):
                while not any(
                    row["record"].get("type") == "result"
                    for row in runner.journal.read()
                ):
                    await asyncio.sleep(0.05)
            records = runner.journal.read()
            echoes = [
                row
                for row in records
                if row["record"].get("uuid") == str(identity.input_id)
                and row["record"].get("isReplay")
            ]
            assert len(echoes) == 1
            assert echoes[0]["record"]["cheese"]["receipt_work_id"] == str(
                identity.work_id
            )
            # Only this isolated test's process is closed; no existing service.
            await runner.close()
        else:
            runner.observe({**writes[0], "isReplay": True})
            runner.journal.close()
        runner_path = runner.state / "records.sqlite"
        journal = Journal(runner_path)
        try:
            echo_sequence = next(
                row["sequence"]
                for row in journal.read()
                if row["record"].get("cheese", {}).get("receipt")
            )
            last_sequence = journal.read()[-1]["sequence"]
        finally:
            journal.close()
        mirror_path = tmp_path / "mirror.sqlite"
        ref = SessionRef(project, topic, identity.recipient_handle, harness=CLAUDE_CODE)

        async def remote(method, params):
            assert method == "events"
            journal = Journal(runner_path)
            try:
                return {"events": journal.read(params["after"])}
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
        injected = []
        settlement_task = asyncio.current_task()
        assert settlement_task is not None

        def fail_commit(session):
            # Only this drain's receipt transaction is faulted. Background jobs
            # commit on other tasks while the native fixture is running.
            try:
                current_task = asyncio.current_task()
            except RuntimeError:
                return
            if current_task is not settlement_task:
                return
            # Installed only around this drain. Reaction reads may have flushed
            # the effects already, so dirty membership cannot gate the fault.
            session.flush()
            injected.append("echo settlement")
            session.execute(text("SELECT 1 / 0"))

        event.listen(Session, "before_commit", fail_commit)
        try:
            try:
                await reading.drain()
            except DBAPIError:
                pass
            else:
                raise AssertionError("The real settlement commit must fail")
        finally:
            event.remove(Session, "before_commit", fail_commit)
            await reading.release()
        assert injected == ["echo settlement"]
        journal = Journal(mirror_path)
        try:
            assert journal.recall("received") == str(last_sequence)
            assert int(journal.recall("landed") or 0) < echo_sequence
        finally:
            journal.close()
        async with factory() as session:
            row = await session.scalar(
                select(NativeInput).where(NativeInput.input_id == identity.input_id)
            )
            assert row.settled_at is None and row.echoed_at is None
            assert (await session.get(Delivery, delivery_id)).state == "sending"
            assert list(await session.scalars(select(BlockReaction))) == []
            for block_id in block_ids:
                assert (await session.get(Block, block_id)).meta[
                    "consumed_turn"
                ] is None

        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _RECOVER,
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
            assert row.settled_at is not None and row.echoed_at is not None
            assert (await session.get(Delivery, delivery_id)).state == "received"
            reactions = list(await session.scalars(select(BlockReaction)))
            assert len(reactions) == 2
            assert {reaction.block_id for reaction in reactions} == set(block_ids)
            for block_id in block_ids:
                assert (await session.get(Block, block_id)).meta[
                    "consumed_turn"
                ] == str(identity.work_id)
        assert len(writes) == 1

    try:
        client.portal.call(run)
    finally:
        if machine:
            machine.close()
            sys.path.remove(scripts)
