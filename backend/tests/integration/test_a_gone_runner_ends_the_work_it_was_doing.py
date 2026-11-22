"""A session whose runner the machine says is gone, in the middle of a work:
the room hears the work ended, and the seat is free for the next message."""

import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from app.domain.agent import runner_reports, session_state, turn_inputs
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven import lifecycle
from app.domain.agent.models import SessionState
from app.domain.agent.reads import Ended, Read, Report, SessionRunning
from app.domain.agent.room import reads as room_reads
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.room.sessions import reading as room_reading
from app.domain.agent.room.sessions.records import Live
from app.domain.agent.service import AgentResult
from app.domain.conversation.models import Conversation
from app.domain.delivery.input_holds import seat_has_unfinished_input
from tests.turn_log import a_topic

SEAT = "cheese-test"
HARNESS = "claude-code"


@pytest.mark.anyio
async def test_the_room_hears_the_work_ended_and_the_seat_is_free(db_factory):
    topic = await a_topic(db_factory)
    async with db_factory() as session:
        project = (await session.get(Conversation, topic)).project_id
    key = session_state.SeatKey(topic, SEAT, HARNESS)
    epoch = await session_state.register(db_factory, key)
    results: list = []
    activity: list = []

    class _Landing:
        async def accept(self, project, topic, work, event, eid, seen, unsolicited):
            if isinstance(event, AgentResult):
                results.append((work, event.is_error))

    async def set_activity(project_id, topic_id, work_id, active, *, agent_handle=None):
        activity.append((work_id, active))

    chat = SimpleNamespace(
        session_factory=db_factory,
        live=SimpleNamespace(unread_inputs={}),
        hook_events=_Landing(),
        _set_hook_activity=set_activity,
    )
    chat.apply_report = lambda report: runner_reports.apply(chat, report)
    chat.report_followed = lambda report: runner_reports.projected(chat, report)
    chat.process_gone = lambda session, epoch: runner_reports.gone(chat, key, epoch)
    chat.confirm_work_abandoned = lambda work: turn_inputs.end_inputs_of_abandoned_work(
        db_factory, work
    )
    svc = RoomSessions(None, HARNESS, None)  # type: ignore[arg-type]
    svc.reader = room_reads.reader(chat)  # type: ignore[arg-type]
    ref = SessionRef(project, topic, SEAT, harness=HARNESS)
    live = Live(ref, None, SEAT, "conv")  # type: ignore[arg-type]
    seat = (topic, SEAT)
    svc.live[seat] = live

    # The session is at work on a message handed to this registration's process.
    work = uuid.uuid4()
    async with db_factory() as session:
        await session.execute(
            text(
                "INSERT INTO native_inputs (id, project_id, conversation_id, "
                "recipient_handle, harness, native_session_id, input_id, "
                "work_id, held_block_ids, released_block_ids, block_ids, "
                "registered_at, handed_epoch) VALUES (:id, :project, :topic, "
                ":seat, :harness, 'conv', :work, :work, '[]', '[]', '[]', now(), "
                ":epoch)"
            ),
            {
                "id": uuid.uuid4(),
                "project": project,
                "topic": topic,
                "seat": SEAT,
                "harness": HARNESS,
                "work": work,
                "epoch": epoch,
            },
        )
        await session.commit()
    running = Report(
        topic, SEAT, HARNESS, epoch, epoch, 1, datetime.now(UTC), SessionRunning(work)
    )
    await room_reading._report(svc, seat, live, running)

    over = await room_reading._take(
        svc, seat, live, Read(None, Ended("exited", epoch=epoch)), asyncio.Event()
    )

    assert over, "the reading of a gone session went on"
    assert results == [(work, True)], "the room did not hear the work ended"
    assert activity[-1] == (work, False)
    async with db_factory() as session:
        state = await session.scalar(
            select(SessionState).where(
                SessionState.conversation_id == topic,
                SessionState.agent_handle == SEAT,
            )
        )
        assert state.worker_status == lifecycle.IDLE
        assert state.exited_at is not None
        assert not await seat_has_unfinished_input(session, topic, SEAT)
