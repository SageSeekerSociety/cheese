"""What follows from a runner's report is done even when part of it fails once.

The database half of a report commits with its claim (``session_state.take``);
what follows (the room hearing the work's result, the work marked over, the
frames told) is done after. When any of that fails, the reading does not
acknowledge the event, the runner delivers it again, and the rest is done
then: the database half is not applied twice, and the result lands once.
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.agent import runner_reports, session_state
from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven import reporting
from app.domain.agent.reads import Report, SessionIdle, SessionRunning
from app.domain.agent.room import reads as room_reads
from app.domain.agent.room.sessions import RoomSessions
from app.domain.agent.room.sessions import reading as room_reading
from app.domain.agent.room.sessions.records import Live
from app.domain.agent.service import AgentResult
from app.domain.conversation.models import Conversation
from tests.turn_log import a_topic

SEAT = "cheese-test"
HARNESS = "claude-code"


class _Accept:
    def __init__(self):
        self.results: list = []
        self.failed = False

    async def accept(self, project, topic, work, event, eid, seen, unsolicited):
        if isinstance(event, AgentResult):
            if not self.failed:
                self.failed = True
                raise RuntimeError("transient: the turn store refused once")
            self.results.append((work, event.text))


@pytest.mark.anyio
async def test_the_result_lands_once_after_the_room_refused_it_once(db_factory):
    topic = await a_topic(db_factory)
    async with db_factory() as session:
        project = (await session.get(Conversation, topic)).project_id
    key = session_state.SeatKey(topic, SEAT, HARNESS)
    epoch = await session_state.register(db_factory, key)

    accept = _Accept()
    activity: list = []

    async def set_activity(project_id, topic_id, work_id, active, *, agent_handle=None):
        activity.append((work_id, active))

    chat = SimpleNamespace(
        session_factory=db_factory,
        live=SimpleNamespace(unread_inputs={}),
        hook_events=accept,
        _set_hook_activity=set_activity,
    )
    chat.apply_report = lambda report: runner_reports.apply(chat, report)
    chat.report_followed = lambda report: runner_reports.projected(chat, report)
    svc = RoomSessions(None, HARNESS, None)  # type: ignore[arg-type]
    svc.reader = room_reads.reader(chat)  # type: ignore[arg-type]
    ref = SessionRef(project, topic, SEAT, harness=HARNESS)
    live = Live(ref, None, SEAT, "conv")  # type: ignore[arg-type]
    seat = (topic, SEAT)
    svc.live[seat] = live

    work = uuid.uuid4()
    now = datetime.now(UTC)
    running = Report(topic, SEAT, HARNESS, epoch, epoch, 1, now, SessionRunning(work))
    idle = Report(
        topic,
        SEAT,
        HARNESS,
        epoch,
        epoch,
        2,
        now,
        SessionIdle(work, AgentResult(text="done", session_id="conv")),
    )
    await room_reading._report(svc, seat, live, running)
    assert svc.work.get(seat) == work

    # First delivery: the database half commits, the room's half fails.
    with pytest.raises(RuntimeError):
        await room_reading._report(svc, seat, live, idle)
    # Redelivered (the reading did not acknowledge it, see the second test).
    await room_reading._report(svc, seat, live, idle)

    # Delivered once more (an acknowledgement lost): nothing happens again.
    await room_reading._report(svc, seat, live, idle)
    assert accept.results == [(work, "done")], "the closing result never landed"
    assert activity.count((work, False)) == 1, "the work was not marked over once"
    assert svc.work.get(seat) is None


@pytest.mark.anyio
async def test_an_event_the_room_refused_is_not_acknowledged():
    events = [
        {
            "epoch": 1,
            "seq": 7,
            "at": datetime.now(UTC).isoformat(),
            "kind": "status",
            "worker_status": "running",
            "work_id": str(uuid.uuid4()),
            "records": 0,
        }
    ]
    asked: list[dict] = []

    async def call(method, params):
        asked.append(dict(params))
        return {"worker_epoch": 1, "events": events, "records": [], "state": {}}

    handed: list = []

    async def hand(work, report):
        handed.append(report.seq)
        if len(handed) == 1:
            raise RuntimeError("room refused")

    async def nothing(*args, **kwargs):
        return None

    whose = reporting.Whose(
        SessionRef(uuid.uuid4(), uuid.uuid4(), SEAT, harness=HARNESS),
        SEAT,
        uuid.uuid4(),
        SEAT,
        HARNESS,
        "Claude Code",
    )
    reading = reporting.Reading(
        whose,
        call,
        epoch=lambda: 1,
        assemble=lambda record: [],
        show=nothing,
        hand=hand,
        records=None,
        controls=nothing,
    )
    with pytest.raises(RuntimeError):
        await reading.drain()
    await reading.drain()
    assert handed == [7, 7]
    assert asked[1]["ack"] == 0
