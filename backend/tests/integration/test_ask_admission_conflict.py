"""A prompt hold racing an answer registration cannot strand a sending row.

Real PostgreSQL locks and the production ChatService registrar are used. External
I/O is a counter reached only after registration, not a native runner in this case.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ValidationError
from app.domain.agent.answer_delivery import offer_answer
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness.claude_code.runtime import ClaudeCodeRuntime
from app.domain.block.models import Block
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import (
    complete_work_inputs,
    record_receipt,
    register_input,
)
from tests.integration.test_native_batch_ownership import _blocks, _delivery, _identity
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


def test_prompt_hold_rolls_back_answer_fence_before_external_io(client, monkeypatch):
    import uuid

    project = uuid.UUID(_project(client, "answer admission conflict"))
    topic = uuid.UUID(_room(client, str(project), "answer admission"))

    async def run():
        factory = client.test_request_factory
        ids = await _blocks(factory, project, topic)
        initial = _identity(project, topic)
        answer = replace(initial, input_id=uuid.uuid4())
        delivery_id, attempt = await _delivery(factory, answer)
        async with factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            delivery.state = "claimed"
            delivery.lease_until = datetime.now(UTC) + timedelta(minutes=2)
            delivery.payload = {"answer_to": str(uuid.uuid4()), "v": 1}
            for block_id in ids:
                block = await session.get(Block, block_id)
                block.meta = {
                    "answer_to": delivery.payload["answer_to"],
                    "delivery_event_id": str(delivery.event_id),
                }
            await session.commit()
        chat = ChatService(
            session_factory=factory,
            base_system_prompt="fixture",
            workspace_root="/unused",
            compute=ComputePool(
                [ClaudeCodeRuntime(SimpleNamespace(name="unused"))], "unused"
            ),
        )
        registrar = chat._input_registrar(
            InputEffects(
                held_block_ids=ids, delivery_id=delivery_id, attempt_id=attempt
            ),
            fence_delivery=True,
        )
        locked, release, entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        pids, external = {}, []
        original = AsyncSession.scalars
        holder = None

        async def pause(session, statement, *args, **kwargs):
            result = await original(session, statement, *args, **kwargs)
            if (
                session is holder
                and getattr(statement, "_for_update_arg", None) is not None
            ):
                locked.set()
                await release.wait()
            return result

        monkeypatch.setattr(AsyncSession, "scalars", pause)

        async def prompt():
            nonlocal holder
            async with factory() as session:
                holder = session
                pids["prompt"] = await session.scalar(text("SELECT pg_backend_pid()"))
                await register_input(session, initial, InputEffects(held_block_ids=ids))
                await session.commit()

        original_scalar = AsyncSession.scalar

        async def record_pid(session, statement, *args, **kwargs):
            if (
                asyncio.current_task().get_name() == "answer-conflict"
                and "answer" not in pids
            ):
                pids["answer"] = await original_scalar(
                    session, text("SELECT pg_backend_pid()")
                )
                entered.set()
            return await original_scalar(session, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncSession, "scalar", record_pid)

        async def answering():
            await locked.wait()
            try:
                await registrar(answer)
            except ValidationError as exc:
                assert "already held" in str(exc)
            else:
                external.append(answer.input_id)

        async def observe():
            await entered.wait()
            async with factory() as session:
                while True:
                    blockers = await session.scalar(
                        text("SELECT pg_blocking_pids(:pid)"), {"pid": pids["answer"]}
                    )
                    if pids["prompt"] in blockers:
                        release.set()
                        return
                    await asyncio.sleep(0.01)

        async with asyncio.timeout(15):
            async with asyncio.TaskGroup() as group:
                group.create_task(prompt())
                group.create_task(answering(), name="answer-conflict")
                group.create_task(observe())
        assert external == []
        async with factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            assert delivery.state == "claimed"
            assert delivery.sent_at is None
            inputs = list(await session.scalars(select(NativeInput)))
            assert len(inputs) == 1
            assert inputs[0].input_id == initial.input_id
            assert inputs[0].delivery_id is None
        # Echo alone must withhold this Delivery, not certify consumption.
        async with factory() as session:
            await record_receipt(
                session, InputReceipt(initial, "native_echo", initial.work_id)
            )
            await session.commit()
        assert await offer_answer(chat, topic, delivery_id, attempt, "answer") is True
        async with factory() as session:
            assert (await session.get(Delivery, delivery_id)).sent_at is None
            await complete_work_inputs(
                session,
                project_id=project,
                topic_id=topic,
                recipient_handle=initial.recipient_handle,
                harness=initial.harness,
                native_session_id=initial.native_session_id,
                work_id=initial.work_id,
                require_registered=True,
                input_ids=(initial.input_id,),
            )
            await session.commit()
        # A dispatcher retry gets a new attempt after the original hold released.
        retry = uuid.uuid4()
        async with factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            delivery.attempt_id = retry
            await session.commit()
        assert await offer_answer(chat, topic, delivery_id, retry, "answer") is True
        async with factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            assert delivery.state == "received" and delivery.sent_at
            assert len(list(await session.scalars(select(NativeInput)))) == 1
        assert external == []

    client.portal.call(run)
