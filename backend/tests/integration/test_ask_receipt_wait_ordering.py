"""An Ask correction resumes after either ordering of wait intent and echo.

Real PostgreSQL, Ask group/wake producers, receipts, the registrar, and the
dispatcher own the boundary. The runner substitutes only external native I/O;
these cases do not prove native-process provenance or behavior.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import CLAUDE_CODE
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.session_host.host import SessionHost
from app.domain.block.ask_groups import AskGroups, parse_questions
from app.domain.delivery.agent import instance_for_seat, record_agent, run_attempt
from app.domain.delivery.ask_receipt_wait import ASK_RECEIPT_WAIT, AskReceiptPending
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.ledger import DeliveryEvent
from app.domain.delivery.models import Delivery, NativeInput
from app.domain.delivery.receipts import complete_work_inputs
from app.domain.notification.models import NotificationType
from tests.integration.conftest import room_agent_seat, session_auth_headers
from tests.integration.test_ask_question_sharing_boundaries import _wake
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


class _OwnedTasks(set):
    """Keep finished tasks observable even after the owner's discard callback."""

    def __init__(self):
        super().__init__()
        self.finished = []

    def add(self, task):
        super().add(task)
        task.add_done_callback(self.finished.append)


class _NativeBoundary:
    owns_sessions = True
    accepting_turns = True

    def __init__(self, factory):
        self.factory = factory
        self._tasks = _OwnedTasks()
        self.submitted = []
        self.external = []

    def submit(self, chat, topic_id, **kwargs):
        self.submitted.append(kwargs["delivery_id"])
        assert topic_id == self.identity.conversation_id
        assert kwargs["delivery_id"] == self.delivery_id
        assert [recipient.handle for recipient in kwargs["addressed"].recipients] == [
            self.identity.recipient_handle
        ]

        async def native_input():
            # The real dispatcher claimed a fresh attempt. The real registrar
            # fences and commits it before the substituted external boundary.
            await chat._input_registrar(
                InputEffects(
                    held_block_ids=(self.wake_id,),
                    delivery_id=kwargs["delivery_id"],
                    attempt_id=kwargs["turn_id"],
                ),
                fence_delivery=True,
            )(self.identity)
            self.external.append((self.identity.input_id, kwargs["turn_id"]))
            await chat.confirm_prompt_receipt(InputReceipt(self.identity, "accepted"))
            await chat.confirm_prompt_receipt(
                InputReceipt(self.identity, "native_echo", self.identity.work_id)
            )

        task = asyncio.create_task(
            run_attempt(
                self.factory,
                kwargs["delivery_id"],
                kwargs["turn_id"],
                native_input(),
                chat=chat,
            )
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return kwargs["turn_id"]

    async def drain(self):
        # Wait only on tasks that are still running. ``asyncio.gather`` over
        # finished tasks alone completes without suspending, so waiting on the
        # whole set would spin on the loop without ever yielding: the queued
        # ``discard`` callbacks would not run, the set would never empty, and
        # the ``timeout`` could not be delivered either.
        async with asyncio.timeout(5):
            while True:
                pending = [task for task in tuple(self._tasks) if not task.done()]
                if not pending:
                    break
                await asyncio.gather(*pending)
            # Every owned task that finished, whether or not its queued
            # ``finished``/``discard`` callbacks have run yet: a failure must
            # still surface here rather than pass unnoticed.
            for task in (*self._tasks, *self._tasks.finished):
                if task.done():
                    task.result()


async def _sentinels(factory, project, topic, seat, other_seat):
    """Use real producer rows; only the uncertain sender outcome is substituted."""
    result = {}
    async with factory() as session:
        for recipient, state in ((other_seat, "pending"), (seat, "uncertain")):
            agent = await instance_for_seat(session, project, recipient)
            assert agent is not None
            event = DeliveryEvent(
                uuid.uuid4(), NotificationType.ROOM_NOTICE, {}, datetime.now(UTC)
            )
            await record_agent(
                session,
                event,
                conversation_id=topic,
                instance_id=agent.id,
                content=f"Unrelated {state} input",
            )
            row = await session.scalar(
                select(Delivery).where(
                    Delivery.event_id == event.id,
                    Delivery.recipient_handle == recipient,
                )
            )
            assert row is not None
            if state == "uncertain":
                row.state = state
                row.attempt_id = uuid.uuid4()
            result[row.id] = (state, row.attempt_id, row.attempts)
        await session.commit()
    return result


@pytest.mark.parametrize(
    "order", ["wait-before-echo", "echo-before-wait", "echo-during-scan"]
)
def test_ask_correction_retries_once_after_its_group_echo(client, monkeypatch, order):
    project = uuid.UUID(_project(client, "Ask receipt ordering"))
    topic = uuid.UUID(_room(client, str(project), "Ask continuation"))
    seat = room_agent_seat(client, str(topic))
    created = client.post(
        f"/projects/{project}/agents",
        json={"handle": "opus"},
    )
    assert created.status_code == 200, created.text
    other_seat = created.json()["data"]["seat_handle"]
    added = client.post(
        f"/topics/{topic}/members",
        json={"handle": other_seat, "role": "member", "actor": "user-1"},
        headers=session_auth_headers("user-1"),
    )
    assert added.status_code == 200, added.text
    assert seat != other_seat

    async def run():
        factory = client.test_request_factory
        runner = _NativeBoundary(factory)
        monkeypatch.setattr("app.domain.agent.pending_messages._runner", runner)
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
        initial = replace(_identity(project, topic, seat), harness=CLAUDE_CODE)
        initial = replace(initial, input_id=initial.work_id)
        prompt_ids = await _blocks(factory, project, topic)
        await chat._input_registrar(InputEffects(held_block_ids=prompt_ids))(initial)
        await chat.confirm_prompt_receipt(InputReceipt(initial, "accepted"))
        await chat.confirm_prompt_receipt(
            InputReceipt(initial, "native_echo", initial.work_id)
        )
        await runner.drain()
        origin = {
            "harness": initial.harness,
            "native_session_id": initial.native_session_id,
            "work_id": str(initial.work_id),
            "recipient_handle": seat,
            "asked_by": seat,
            "asked": "user-1",
        }
        group_id = str(uuid.uuid4())
        async with factory() as session:
            await AskGroups(session).create(
                project_id=project,
                conversation_id=topic,
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
            await complete_work_inputs(
                session,
                project_id=project,
                conversation_id=topic,
                recipient_handle=seat,
                harness=initial.harness,
                native_session_id=initial.native_session_id,
                work_id=initial.work_id,
                require_registered=True,
                input_ids=(initial.input_id,),
            )
            await session.commit()
        first = replace(initial, input_id=uuid.uuid4(), work_id=uuid.uuid4())
        wake1, delivery1, attempt1 = await _wake(factory, initial, origin, group_id, 1)
        await chat._input_registrar(
            InputEffects(
                held_block_ids=(wake1,), delivery_id=delivery1, attempt_id=attempt1
            ),
            fence_delivery=True,
        )(first)
        await chat.confirm_prompt_receipt(InputReceipt(first, "accepted"))
        unrelated = replace(first, input_id=uuid.uuid4())
        await chat._input_registrar(InputEffects())(unrelated)
        await chat.confirm_prompt_receipt(InputReceipt(unrelated, "accepted"))
        await chat.confirm_prompt_receipt(
            InputReceipt(unrelated, "native_echo", unrelated.work_id)
        )
        await runner.drain()
        wake2, delivery2, attempt2 = await _wake(factory, initial, origin, group_id, 2)
        correction = replace(first, input_id=uuid.uuid4())
        runner.identity = correction
        runner.delivery_id = delivery2
        runner.wake_id = wake2
        sentinels = await _sentinels(factory, project, topic, seat, other_seat)
        blocked = []
        first_scan_read = asyncio.Event()
        second_scan_entered = asyncio.Event()
        release_first_scan = asyncio.Event()
        scans = []

        if order == "echo-during-scan":

            @asynccontextmanager
            async def gated_factory():
                async with factory() as session:
                    task = asyncio.current_task()
                    if task.get_name().startswith("ask-receipt:"):
                        pid = await session.scalar(text("SELECT pg_backend_pid()"))
                        scans.append((task, pid))
                        if len(scans) == 1:
                            original_commit = session.commit

                            async def gate_commit():
                                # The first scan holds V2's Delivery lock and
                                # has read the genuinely unproven V1 input.
                                first_scan_read.set()
                                await release_first_scan.wait()
                                await original_commit()

                            session.commit = gate_commit
                        elif len(scans) == 2:
                            second_scan_entered.set()
                    yield session

            monkeypatch.setattr(chat, "_sessions", gated_factory)

        async def assert_waiting():
            async with factory() as session:
                row = await session.get(Delivery, delivery2)
                assert row.state == "pending"
                assert row.retry_at > datetime.now(UTC)
                assert row.sent_at is None
                assert row.payload[ASK_RECEIPT_WAIT]["ask_group"] == group_id
                assert (
                    await session.scalar(
                        select(NativeInput.id).where(
                            NativeInput.delivery_id == delivery2
                        )
                    )
                    is None
                )
                first_row = await session.scalar(
                    select(NativeInput).where(NativeInput.input_id == first.input_id)
                )
                assert first_row.accepted_at is not None
                assert first_row.echoed_at is None
                assert first_row.completed_at is None
            assert runner.submitted == []
            assert runner.external == []

        async def refused_input():
            try:
                await chat._input_registrar(
                    InputEffects(
                        held_block_ids=(wake2,),
                        delivery_id=delivery2,
                        attempt_id=attempt2,
                    ),
                    fence_delivery=True,
                )(correction)
            except AskReceiptPending as exc:
                blocked.append(exc)
                if order == "echo-before-wait":
                    # The registrar has rolled back, but run_attempt has not yet
                    # stored wait intent. Finish the receipt's scan in this gap.
                    async with factory() as session:
                        row = await session.get(Delivery, delivery2)
                        assert row.state == "claimed"
                        assert ASK_RECEIPT_WAIT not in row.payload
                    await chat.confirm_prompt_receipt(
                        InputReceipt(first, "native_echo", first.work_id)
                    )
                    await runner.drain()
                    assert runner.submitted == []
                    assert runner.external == []
                raise
            else:
                raise AssertionError("V2 was admitted before its group wake was echoed")

        try:
            await run_attempt(factory, delivery2, attempt2, refused_input(), chat=chat)
            assert len(blocked) == 1
            assert blocked[0].identity == correction
            assert blocked[0].delivery_id == delivery2
            assert blocked[0].attempt_id == attempt2
            assert blocked[0].group_id == group_id
            if order == "echo-during-scan":
                async with asyncio.timeout(5):
                    await first_scan_read.wait()
                    await assert_waiting()
                    await chat.confirm_prompt_receipt(
                        InputReceipt(first, "native_echo", first.work_id)
                    )
                    await second_scan_entered.wait()
                    async with factory() as observer:
                        while not scans[1][0].done():
                            blockers = await observer.scalar(
                                text("SELECT pg_blocking_pids(:pid)"),
                                {"pid": scans[1][1]},
                            )
                            if scans[0][1] in blockers:
                                assert scans[0][1] != scans[1][1]
                                break
                            await asyncio.sleep(0.01)
                    # A SKIP LOCKED scan finishes here while A still holds V2;
                    # a waiting scan is observed blocked by A's actual PID.
                    # Release only after one of these real outcomes is known.
                    assert runner.submitted == []
                    assert runner.external == []
        finally:
            if order == "echo-during-scan":
                release_first_scan.set()
                try:
                    await runner.drain()
                finally:
                    monkeypatch.setattr(chat, "_sessions", factory)
                    pending = tuple(runner._tasks)
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    runner._tasks.clear()
        if order != "echo-during-scan":
            await runner.drain()
        if order == "wait-before-echo":
            await assert_waiting()
            # Even an echo for this exact room, seat, session and current work
            # cannot wake V2 when the echoed input does not own this group's wake.
            await chat.confirm_prompt_receipt(
                InputReceipt(unrelated, "native_echo", unrelated.work_id)
            )
            await runner.drain()
            await assert_waiting()
            await chat.confirm_prompt_receipt(
                InputReceipt(first, "native_echo", first.work_id)
            )
            await runner.drain()

        assert runner.submitted == [delivery2]
        assert len(runner.external) == 1
        assert runner.external[0][0] == correction.input_id
        assert runner.external[0][1] != attempt2
        async with factory() as session:
            row = await session.get(Delivery, delivery2)
            assert row.state == "received"
            assert row.sent_at is not None
            assert row.attempts == 1
            assert row.attempt_id == runner.external[0][1]
            assert row.retry_at is None
            inputs = list(
                await session.scalars(
                    select(NativeInput).where(NativeInput.conversation_id == topic)
                )
            )
            assert {row.input_id for row in inputs} == {
                initial.input_id,
                first.input_id,
                unrelated.input_id,
                correction.input_id,
            }
            corrected = next(
                row for row in inputs if row.input_id == correction.input_id
            )
            asking = next(row for row in inputs if row.input_id == initial.input_id)
            first_row = next(row for row in inputs if row.input_id == first.input_id)
            assert asking.completed_at is not None
            assert first_row.execution_work_id == first.work_id
            assert first_row.echoed_at is not None
            assert first_row.settled_at is not None
            assert first_row.completed_at is None
            assert corrected.delivery_id == delivery2
            assert corrected.attempt_id == runner.external[0][1]
            assert corrected.execution_work_id == first.work_id
            assert corrected.echoed_at is not None
            assert corrected.settled_at is not None
            assert corrected.completed_at is None
            for delivery_id, expected in sentinels.items():
                sentinel = await session.get(Delivery, delivery_id)
                assert (
                    sentinel.state,
                    sentinel.attempt_id,
                    sentinel.attempts,
                ) == expected
                assert sentinel.sent_at is None
        # A duplicate real receipt may schedule a scan, but cannot send again.
        await chat.confirm_prompt_receipt(
            InputReceipt(first, "native_echo", first.work_id)
        )
        await runner.drain()
        assert runner.submitted == [delivery2]
        assert len(runner.external) == 1

    client.portal.call(run)
