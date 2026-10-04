"""An Ask answer waits for the conversation that asked, and stops poisoning the room.

Real PostgreSQL, the real Ask settlement/wake producers, the real attempt settle
and the real prompt hold-out. Only the native turn boundary is substituted:
these cases do not prove native-process provenance or behavior.
"""

import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.domain.agent.answer_delivery import run_with_answer_offer
from app.domain.agent.chat import ChatService
from app.domain.agent.compute import ComputePool
from app.domain.agent.harness import CLAUDE_CODE, SessionRef
from app.domain.agent.room.sessions import Live, RoomSessions
from app.domain.agent.session_host.contract import SessionRef as CoreRef
from app.domain.agent.session_host.host import SessionHost
from app.domain.block.ask_groups import AskGroups, parse_questions
from app.domain.block.models import AuthorType
from app.domain.block.repositories import BlockRepository
from app.domain.delivery.agent import run_attempt
from app.domain.delivery.ask_session_wait import (
    ASK_SESSION_WAIT,
    ASK_SESSION_WAIT_REASON,
    release_conversation_wait,
    waiting_ask_blocks,
)
from app.domain.delivery.input_identity import InputEffects, InputReceipt
from app.domain.delivery.models import Delivery
from app.domain.delivery.receipts import complete_work_inputs
from tests.integration.conftest import room_agent_seat
from tests.integration.test_ask_question_sharing_boundaries import _wake
from tests.integration.test_native_batch_ownership import _blocks, _identity
from tests.integration.test_same_handle_note_and_timed_delivery import _project, _room


class _DispatcherRunner:
    """The dispatcher's own runner: only its two waits are neutralized."""

    async def _wait_to_start(self):
        return None

    async def _wait_for_replay(self, chat_service, topic_id, turn_id):
        return None


def _fixture(client, name):
    """The roster reads a test needs before any event loop is involved."""
    project = uuid.UUID(_project(client, name))
    topic = uuid.UUID(_room(client, str(project), "Ask conversation gone"))
    return project, topic, room_agent_seat(client, str(topic))


async def _answer(client, project, topic, seat, *, attach):
    """A room whose agent asked a question and got an answer, version 1.

    ``attach``: whether the conversation that asked is still live for the seat.
    ``False`` is a seat whose asking conversation is gone — the machine was
    rebuilt, the session exited — and the answer has nowhere to go.
    """
    factory = client.test_request_factory
    sessions = RoomSessions(SimpleNamespace(name="unused"), CLAUDE_CODE, SessionHost())
    chat = ChatService(
        session_factory=factory,
        base_system_prompt="fixture",
        workspace_root="/unused",
        compute=ComputePool([sessions], "unused"),
    )
    initial = replace(_identity(project, topic, seat), harness=CLAUDE_CODE)
    initial = replace(initial, input_id=initial.work_id)
    prompt_ids = await _blocks(factory, project, topic)
    await chat._input_registrar(InputEffects(held_block_ids=prompt_ids))(initial)
    await chat.confirm_prompt_receipt(InputReceipt(initial, "accepted"))
    await chat.confirm_prompt_receipt(
        InputReceipt(initial, "native_echo", initial.work_id)
    )
    if attach:
        sessions.live[(topic, seat)] = Live(
            SessionRef(project, topic, seat, harness=CLAUDE_CODE),
            CoreRef(CLAUDE_CODE, "core"),
            seat,
            initial.native_session_id,
        )
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
    wake, delivery, attempt = await _wake(factory, initial, origin, group_id, 1)
    return chat, initial, wake, delivery, attempt


def test_a_gone_ask_conversation_leaves_the_answer_waiting(client):
    """No turn for a conversation that is not there, and no 30 s retry either."""
    project, topic, seat = _fixture(client, "Ask answer waits")

    async def run():
        chat, initial, wake, delivery, attempt = await _answer(
            client, project, topic, seat, attach=False
        )
        started = []

        async def turn():
            started.append(True)

        answer = turn()
        await run_attempt(
            client.test_request_factory,
            delivery,
            attempt,
            run_with_answer_offer(
                _DispatcherRunner(), chat, topic, delivery, attempt, "A", answer
            ),
            chat=chat,
        )
        assert started == [], "a turn ran for a conversation that is gone"
        assert answer.cr_frame is None, "the turn was left unclosed"

        async with client.test_request_factory() as session:
            row = await session.get(Delivery, delivery)
            assert row.state == "pending"
            assert row.sent_at is None
            assert row.payload[ASK_SESSION_WAIT] == initial.native_session_id
            assert row.last_error == ASK_SESSION_WAIT_REASON
            # The retry is a liveness question, not a 30 s dispatch: the row
            # waits longer than the dispatcher's own retry interval.
            assert row.retry_at > datetime.now(UTC) + timedelta(seconds=120)
            # The answer is held out of this seat's prompts; the person's own
            # message in the room is not.
            ordinary = await BlockRepository(session).add(
                project_id=project,
                topic_id=topic,
                author="user-1",
                author_type=AuthorType.participant,
                content="a person's own message",
                meta={"consumed_turn": None},
            )
            await session.commit()
            held = await waiting_ask_blocks(
                session, topic_id=topic, recipient_handle=seat
            )
            assert held == {wake}
            assert ordinary.id not in held

        # Once the conversation is live again the answer is no longer waiting
        # and no longer held: the next attempt dispatches it.
        await release_conversation_wait(client.test_request_factory, delivery)
        async with client.test_request_factory() as session:
            row = await session.get(Delivery, delivery)
            assert ASK_SESSION_WAIT not in row.payload
            assert (
                await waiting_ask_blocks(session, topic_id=topic, recipient_handle=seat)
                == set()
            )

    client.portal.call(run)


def test_a_live_ask_conversation_dispatches_the_answer_as_before(client):
    """The wait is only for a conversation that is gone: a live one is untouched."""
    project, topic, seat = _fixture(client, "Ask answer waits live")

    async def run():
        chat, initial, wake, delivery, attempt = await _answer(
            client, project, topic, seat, attach=True
        )
        started = []

        async def turn():
            started.append(True)

        answer = turn()
        await run_attempt(
            client.test_request_factory,
            delivery,
            attempt,
            run_with_answer_offer(
                _DispatcherRunner(), chat, topic, delivery, attempt, "A", answer
            ),
            chat=chat,
        )
        assert started == [True], "a live conversation was not given its answer"
        async with client.test_request_factory() as session:
            row = await session.get(Delivery, delivery)
            assert ASK_SESSION_WAIT not in row.payload
            assert row.last_error != ASK_SESSION_WAIT_REASON

    client.portal.call(run)
