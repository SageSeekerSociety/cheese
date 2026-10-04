"""Question sharing cannot turn registration hints into native work ownership.

Real PostgreSQL, Ask group/wake producers, receipts and the ChatService registrar
own the boundary. The post-registration counter stands in for external native I/O;
these cases do not prove native-process provenance or behavior.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.core.errors import ValidationError
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from app.domain.block.ask_groups import AskGroups, parse_questions
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.ask_wake import record_ask_wake
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import (
    complete_work_inputs,
    record_receipt,
    register_input,
)
from tests.integration.conftest import room_agent_seat
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


async def _wake(factory, initial, origin, group_id, version):
    """Use the real settlement/wake producers; reserve its dispatcher attempt."""
    async with factory() as session:
        groups = AskGroups(session)
        rows = await groups.read(initial.topic_id, initial.recipient_handle, group_id)
        rows, settlement, replay = await groups.settle(
            topic_id=initial.topic_id,
            asked_by=initial.recipient_handle,
            group_id=group_id,
            author="user-1",
            body={
                "client_op_id": f"group-{version}",
                "expect_version": version - 1,
                "answered": [
                    {
                        "block_id": str(rows[0].id),
                        "kind": "option",
                        "option": "A" if version == 1 else "B",
                        "client_op_id": f"answer-{version}",
                        "expect_version": version - 1,
                    }
                ],
                "later": [],
                "unanswered": [],
            },
        )
        assert not replay
        event_id = uuid.UUID(settlement["delivery_event_id"])
        recipient = await record_ask_wake(
            session,
            project_id=initial.project_id,
            topic_id=initial.topic_id,
            origin=origin,
            event_id=event_id,
            content=f"Answer version {version}",
            payload={
                "ask_group": group_id,
                "answer_to": str(rows[0].id),
                "v": version,
                "block_ids": [str(row.id) for row in rows],
            },
        )
        assert recipient is not None
        wake = await BlockRepository(session).add(
            project_id=initial.project_id,
            topic_id=initial.topic_id,
            author="user-1",
            author_type=AuthorType.participant,
            content=f"Answer version {version}",
            meta={
                "answer_group": group_id,
                "answer_to": str(rows[0].id),
                "delivery_event_id": str(event_id),
                "agent_recipient": recipient,
            },
        )
        delivery = await session.scalar(
            select(Delivery).where(
                Delivery.event_id == event_id,
                Delivery.recipient_handle == initial.recipient_handle,
            )
        )
        assert delivery is not None
        # The existing owning admission test reserves the same three fields.
        # Native ownership/evidence is never set directly on NativeInput rows.
        delivery.state = "claimed"
        delivery.attempt_id = uuid.uuid4()
        delivery.lease_until = datetime.now(UTC) + timedelta(minutes=2)
        result = wake.id, delivery.id, delivery.attempt_id
        await session.commit()
        return result


@pytest.mark.parametrize(
    "boundary",
    [
        "unproven-origin",
        "completed-origin",
        "other-work-holder",
        "shared-wake",
        "unproven-continuation",
    ],
)
def test_question_sharing_refuses_unproven_or_conflicting_batches(client, boundary):
    project = uuid.UUID(_project(client, "Ask sharing safety"))
    topic = uuid.UUID(_room(client, str(project), "Ask sharing boundary"))
    seat = room_agent_seat(client, str(topic))

    async def run():
        factory = client.test_request_factory
        initial = replace(_identity(project, topic, seat), harness=CLAUDE_CODE)
        initial = replace(initial, input_id=initial.work_id)
        prompt_ids = await _blocks(factory, project, topic)
        async with factory() as session:
            await register_input(
                session, initial, InputEffects(held_block_ids=prompt_ids)
            )
            await session.commit()
        if boundary != "unproven-origin":
            for receipt in (
                InputReceipt(initial, "accepted"),
                InputReceipt(initial, "native_echo", initial.work_id),
            ):
                async with factory() as session:
                    assert await record_receipt(session, receipt) is not None
                    await session.commit()
        origin = {
            "harness": initial.harness,
            "native_session_id": initial.native_session_id,
            "work_id": str(initial.work_id),
            "recipient_handle": seat,
            "asked_by": seat,
            "asked": "user-1",
            "task_id": None,
        }
        group_id = str(uuid.uuid4())
        async with factory() as session:
            await AskGroups(session).create(
                project_id=project,
                topic_id=topic,
                asked_by=seat,
                group_id=group_id,
                questions=parse_questions(
                    {
                        "questions": [
                            {
                                "question": "Continue?",
                                "options": [{"text": "A"}, {"text": "B"}],
                            }
                        ]
                    }
                ),
                asked="user-1",
                origin=origin,
            )
            await session.commit()
        chat = ChatService(
            session_factory=factory,
            base_system_prompt="fixture",
            workspace_root="/unused",
            compute=ComputePool(
                [
                    RoomSessions(
                        SimpleNamespace(name="unused"), CLAUDE_CODE, SessionHost()
                    )
                ],
                "unused",
            ),
        )
        if boundary == "unproven-continuation":
            # The asking work genuinely ended. Its echoed prompt cannot prove
            # that a different continuation execution read an accepted answer.
            async with factory() as session:
                await complete_work_inputs(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=seat,
                    harness=initial.harness,
                    native_session_id=initial.native_session_id,
                    work_id=initial.work_id,
                    require_registered=True,
                    input_ids=(initial.input_id,),
                )
                await session.commit()
        first = replace(initial, input_id=uuid.uuid4())
        if boundary == "other-work-holder":
            # Another work in this original session is a legal first registration.
            # Its target work is only a reservation hint, not an execution owner.
            first = replace(first, work_id=uuid.uuid4())
        elif boundary == "unproven-continuation":
            # V1 and V2 target the same new work in the pinned original session.
            # V1 will be accepted below but never acquire native echo proof.
            first = replace(first, work_id=uuid.uuid4())
        wake1, delivery1, attempt1 = await _wake(factory, initial, origin, group_id, 1)
        await chat._input_registrar(
            InputEffects(
                held_block_ids=(wake1,),
                delivery_id=delivery1,
                attempt_id=attempt1,
            ),
            fence_delivery=True,
        )(first)
        async with factory() as session:
            assert (
                await record_receipt(session, InputReceipt(first, "accepted"))
                is not None
            )
            await session.commit()
        if boundary == "completed-origin":
            # Only the echoed prompt belongs to this native completion. V1 is
            # still unread and retains its separate Question/wake reservation.
            async with factory() as session:
                await complete_work_inputs(
                    session,
                    project_id=project,
                    topic_id=topic,
                    recipient_handle=seat,
                    harness=initial.harness,
                    native_session_id=initial.native_session_id,
                    work_id=initial.work_id,
                    require_registered=True,
                    input_ids=(initial.input_id,),
                )
                await session.commit()
        wake2, delivery2, attempt2 = await _wake(factory, initial, origin, group_id, 2)
        correction = replace(initial, input_id=uuid.uuid4())
        if boundary == "unproven-continuation":
            correction = replace(correction, work_id=first.work_id)
        held = (wake2, wake1) if boundary == "shared-wake" else (wake2,)
        registrar = chat._input_registrar(
            InputEffects(
                held_block_ids=held,
                delivery_id=delivery2,
                attempt_id=attempt2,
            ),
            fence_delivery=True,
        )
        external = []

        async def refused():
            with pytest.raises(ValidationError, match="already held"):
                await registrar(correction)
                external.append(correction.input_id)
            async with factory() as session:
                inputs = list(
                    await session.scalars(
                        select(NativeInput).where(NativeInput.topic_id == topic)
                    )
                )
                assert {row.input_id for row in inputs} == {
                    initial.input_id,
                    first.input_id,
                }
                delivery = await session.get(Delivery, delivery2)
                assert delivery.state == "claimed"
                assert delivery.sent_at is None
            assert external == []

        await refused()
        if boundary == "unproven-origin":
            async with factory() as session:
                assert (
                    await record_receipt(session, InputReceipt(initial, "accepted"))
                    is not None
                )
                await session.commit()
            await refused()

    client.portal.call(run)
