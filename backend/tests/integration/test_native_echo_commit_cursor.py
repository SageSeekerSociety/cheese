"""Real PostgreSQL commit abort keeps a runner echo replayable by a new reader.

The runner handles a native-shaped echo after stdin substitution, not a running
Claude/model. The child uses a new ChatService and real mirror/subscription.
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime

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
async def run():
    engine = create_async_engine(sys.argv[1])
    chat = ChatService.__new__(ChatService)
    chat._sessions = async_sessionmaker(engine, expire_on_commit=False)
    chat._unread_inputs = {}
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
        announce=noop, receipts=chat.confirm_prompt_receipt)
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


def test_echo_commit_abort_replays_same_identity_in_new_chat_process(client, tmp_path):
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
        delivery_id, attempt_id = uuid.uuid4(), uuid.uuid4()
        block_ids = (uuid.uuid4(), uuid.uuid4())
        async with factory() as session:
            for block_id in block_ids:
                session.add(
                    Block(
                        id=block_id,
                        project_id=project,
                        topic_id=topic,
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
                    topic_id=topic,
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

        runner = Runner(tmp_path / "runner")
        runner.session_id = identity.native_session_id
        writes = []

        async def write(record):
            # This observation is before the external boundary.
            async with factory() as session:
                row = await session.scalar(
                    select(NativeInput).where(NativeInput.input_id == identity.input_id)
                )
                assert row is not None and row.settled_at is None
            writes.append(record)

        runner._write = write
        await runner.send(
            str(identity.input_id), "answer", work_id=str(identity.work_id)
        )
        runner.observe({**writes[0], "isReplay": True})
        runner.journal.close()
        runner_path = tmp_path / "runner" / "records.sqlite"
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
        )
        injected = []

        def fail_commit(session):
            if any(
                isinstance(row, NativeInput) and row.settled_at is not None
                for row in session.dirty
            ):
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
            assert journal.recall("received") == "1"
            assert int(journal.recall("landed") or 0) == 0
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
        assert json.loads(stdout)["landed"] == "1"
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

    client.portal.call(run)
