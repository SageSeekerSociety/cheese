"""Legacy-shaped retained log → real PG abort → reconstructed reader.

This is protocol reconciliation evidence, not a native binary or full executor.
Missing main-era receipt identity intentionally remains unresolved.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.domain.agent.chat import ChatService
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.block.models import Block, consumed_turn
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import NativeInput
from app.domain.delivery.receipts import held_blocks, record_receipt, register_input
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


@pytest.mark.parametrize("format", ["unstamped", "draft-stamp"])
def test_legacy_completion_failure_keeps_cursor_and_reconstructed_reader_settles(
    client, tmp_path, format
):
    project = uuid.UUID(_project(client, "legacy completion"))
    topic = uuid.UUID(_room(client, str(project), "retained execution"))

    async def run():
        factory = client.test_request_factory
        identity = replace(_identity(project, topic), harness=CLAUDE_CODE)
        actual = uuid.uuid4()
        ids = await _blocks(factory, project, topic)
        async with factory() as session:
            await register_input(session, identity, InputEffects(held_block_ids=ids))
            await record_receipt(session, InputReceipt(identity, "native_echo"))
            await session.commit()
        owner = {
            "work_id": str(actual),
            "agent_handle": identity.recipient_handle,
            "unsolicited": True,
        }
        echo = {
            "type": "user",
            "uuid": str(identity.input_id),
            "isReplay": True,
            "session_id": identity.native_session_id,
            "cheese": {
                **owner,
                "turn_start": True,
                "receipt": True,
                "receipt_session_id": identity.native_session_id,
                "receipt_work_id": str(identity.work_id),
            },
        }
        result = {
            "type": "result",
            "session_id": identity.native_session_id,
            "is_error": False,
            "cheese": dict(owner),
        }
        if format == "draft-stamp":
            result["cheese"].update(
                work_completed=True, completion_session_id=identity.native_session_id
            )
        at = (datetime.now(UTC) - timedelta(days=3)).isoformat()
        entries = [
            {"sequence": n, "at": at, "record": rec}
            for n, rec in enumerate([echo, result], 1)
        ]
        path = tmp_path / "mirror.sqlite"
        journal = Journal(path)
        journal.import_records(entries, {})
        journal.acknowledge(1)
        journal.close()

        async def call(method, params):
            assert method == "events"
            return {"events": []}

        async def unused(*args, **kwargs):
            pass

        def reader(fail):
            chat = ChatService.__new__(ChatService)
            chat._sessions, chat._unread_inputs = factory, {}

            async def complete(value):
                def abort(session):
                    session.flush()
                    session.execute(text("SELECT 1 / 0"))

                if fail:
                    event.listen(Session, "before_commit", abort)
                try:
                    await chat.confirm_work_completion(value)
                finally:
                    if fail:
                        event.remove(Session, "before_commit", abort)

            return Subscription(
                SessionRef(
                    project, topic, identity.recipient_handle, harness=CLAUDE_CODE
                ),
                path,
                call,
                unused,
                unused,
                session_id=identity.native_session_id,
                recipient_handle=identity.recipient_handle,
                announce=unused,
                receipts=chat.confirm_prompt_receipt,
                completions=complete,
                input_protocol=None,
            )

        reading = reader(True)
        try:
            with pytest.raises(DBAPIError):
                await reading.drain()
        finally:
            await reading.release()
        journal = Journal(path)
        assert journal.recall("landed") == "1"
        journal.close()
        async with factory() as session:
            row = await session.scalar(select(NativeInput))
            assert row.execution_work_id == actual
            assert row.released_block_ids == []
            assert await held_blocks(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=identity.recipient_handle,
            ) == set(ids)
        recovered = reader(False)
        try:
            await recovered.drain()
            await recovered.drain()
        finally:
            await recovered.release()
        journal = Journal(path)
        assert journal.recall("landed") == "2"
        journal.close()
        async with factory() as session:
            row = await session.scalar(select(NativeInput))
            assert row.execution_work_id == actual
            assert set(row.released_block_ids) == {str(value) for value in ids}
            blocks = list(await session.scalars(select(Block).where(Block.id.in_(ids))))
            assert {consumed_turn(block) for block in blocks} == {str(actual)}

    client.portal.call(run)
