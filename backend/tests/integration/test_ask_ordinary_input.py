"""PostgreSQL ordinary-input fences, with fixture native origin and evidence.

No native executor is run here; clean completion evidence is explicitly supplied.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.domain.block.models import Block
from app.domain.delivery.answer_ownership import reconcile_answer
from app.domain.delivery.ask_receipts import ask_receipt
from app.domain.delivery.input_identity import InputEffects, InputIdentity, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import (
    complete_work_inputs,
    record_receipt,
    register_input,
)
from tests.integration import test_ask_groups
from tests.integration.test_ask_groups import settle, submission

group = test_ask_groups.group


def test_replacement_cannot_register_an_ordinary_prompt_with_original_ask(
    client, group
):
    data, _ = group
    response = settle(client, data, submission(data))
    assert response.status_code == 200, response.text
    result = response.json()["data"]

    async def run():
        factory = client.test_request_factory
        async with factory() as session:
            identity, effects, delivery = await ordinary(session, result)
            delivery_id = delivery.id
            with pytest.raises(ValidationError, match="replacement"):
                await register_input(
                    session,
                    replace(identity, native_session_id="replacement-B"),
                    effects,
                )
            await session.rollback()
        async with factory() as session:
            assert await session.scalar(select(NativeInput)) is None
            assert (await session.get(Delivery, delivery_id)).state == "pending"

    client.portal.call(run)


def test_original_ordinary_prompt_projects_echo_and_completed_receipts(client, group):
    data, _ = group
    response = settle(client, data, submission(data))
    assert response.status_code == 200, response.text
    result = response.json()["data"]

    async def run():
        factory = client.test_request_factory
        async with factory() as session:
            identity, effects, delivery = await ordinary(session, result)
            delivery_id = delivery.id
            await register_input(session, identity, effects)
            await session.commit()
        async with factory() as session:
            await record_receipt(
                session, InputReceipt(identity, "native_echo", identity.work_id)
            )
            await session.commit()
        async with factory() as session:
            delivery = await session.get(Delivery, delivery_id)
            attempt = uuid.uuid4()
            delivery.state = "claimed"
            delivery.attempt_id = attempt
            delivery.lease_until = datetime.now(UTC) + timedelta(minutes=1)
            await session.commit()
        async with factory() as session:
            # Echo cannot consume held ordinary input or confirm a whole work.
            assert await reconcile_answer(session, delivery_id, attempt)
            await session.commit()
            assert (await receipt(session, identity, delivery.event_id))[
                "completed_at"
            ] is None
        async with factory() as session:
            await complete_work_inputs(
                session,
                project_id=identity.project_id,
                conversation_id=identity.conversation_id,
                recipient_handle=identity.recipient_handle,
                harness=identity.harness,
                native_session_id=identity.native_session_id,
                work_id=identity.work_id,
                require_registered=True,
                input_ids=(identity.input_id,),
            )
            await session.commit()
        async with factory() as session:
            assert await reconcile_answer(session, delivery_id, attempt)
            await session.commit()
        async with factory() as session:
            actual = await receipt(session, identity, delivery.event_id)
            assert actual["state"] == "received"
            assert actual["received_at"] and actual["completed_at"]
            row = await session.scalar(select(NativeInput))
            assert row.delivery_id is None and row.event_id is None
            assert set(row.released_block_ids) == {
                str(b) for b in effects.held_block_ids
            } | set(data["group"]["members"])

    client.portal.call(run)


async def ordinary(session, result):
    event_id = uuid.UUID(result["settlement"]["delivery_event_id"])
    delivery = await session.scalar(
        select(Delivery).where(Delivery.event_id == event_id)
    )
    wake = await session.scalar(
        select(Block).where(
            Block.meta["delivery_event_id"].as_string() == str(event_id)
        )
    )
    origin = delivery.payload["ask_origin"]
    identity = InputIdentity(
        project_id=wake.project_id,
        conversation_id=wake.conversation_id,
        recipient_handle=origin["recipient_handle"],
        harness=origin["harness"],
        native_session_id=origin["native_session_id"],
        input_id=uuid.uuid4(),
        work_id=uuid.uuid4(),
    )
    return identity, InputEffects(held_block_ids=(wake.id,)), delivery


async def receipt(session, identity, event_id):
    return await ask_receipt(
        session,
        event_id=event_id,
        project_id=identity.project_id,
        topic_id=identity.conversation_id,
        recipient=identity.recipient_handle,
    )
