"""An unread input stops holding its seat only when the process it was handed
to can no longer read it.

Rules a person can state without the code:

- an input handed to a process that has since exited will never be read: it
  no longer holds its seat, and the message it carried goes to the next turn;
- an exit of one process says nothing about an input handed to another, even
  one that replaced it a moment later and even if the exit is stamped by a
  runner whose clock runs ahead: that input stays held, or the same message
  would go into a second input while the first can still be read;
- an input on a session whose machine the platform destroyed will never be
  read either, whichever process it was handed to.

Real PostgreSQL; the reports are built as the reading builds them, and the
handing goes through the session host's own send path.
"""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domain.agent.device_hub import DeviceCallError
from app.domain.agent.reads import SessionExited
from app.domain.agent.session_host.contract import Prompt, SessionRef
from app.domain.agent.session_host.host import SessionHost
from app.domain.agent.session_host.speaking import send
from app.domain.agent_session.models import AgentSession
from app.domain.block.models import Block, BlockKind
from app.domain.conversation.models import Conversation
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.receipts import held_blocks, register_input
from app.domain.event_author import AuthorType
from tests.integration.test_a_runner_report_is_applied_once import (
    HARNESS,
    SEAT,
    SESSION,
    _Seat,
)
from tests.turn_log import a_topic, open_turn


async def _seat(factory) -> _Seat:
    topic = await a_topic(factory)
    async with factory() as session:
        conversation = await session.get(Conversation, topic)
        assert conversation is not None
        return _Seat(factory, topic, conversation.project_id)


async def _registered(seat: _Seat) -> tuple[InputIdentity, uuid.UUID]:
    """A person's message, and the input registered to carry it into a turn."""
    work = await open_turn(seat.factory, seat.topic, delivered=True)
    async with seat.factory() as session:
        message = Block(
            id=uuid.uuid4(),
            project_id=seat.project,
            conversation_id=seat.topic,
            kind=BlockKind.message,
            author_type=AuthorType.participant,
            author="u",
            content="perform once",
            meta={"consumed_turn": None},
        )
        session.add(message)
        await session.commit()
        identity = InputIdentity(
            seat.project, seat.topic, SEAT, HARNESS, SESSION, uuid.uuid4(), work
        )
        await register_input(
            session, identity, InputEffects(held_block_ids=(message.id,))
        )
        await session.commit()
    return identity, message.id


async def _held(seat: _Seat, message: uuid.UUID) -> tuple[bool, bool]:
    """Whether the seat is held, and whether the message is kept from a prompt."""
    async with seat.factory() as session:
        return (
            await seat_has_unfinished_input(session, seat.topic, SEAT),
            message
            in await held_blocks(
                session,
                project_id=seat.project,
                topic_id=seat.topic,
                recipient_handle=SEAT,
            ),
        )


@pytest.mark.anyio
async def test_an_input_handed_on_after_its_first_process_exited_stays_held(
    business_db_factory,
):
    """写给 A 进程的那一刻 A 退出了，平台重启出 B 并把这条输入交给了 B：A 的退出
    说的不是这条输入，B 还留着它没读，座位照旧按住。"""
    seat = await _seat(business_db_factory)
    old = await seat.register()
    identity, message = await _registered(seat)
    running = SimpleNamespace(
        spec=object(),
        access=object(),
        answering=True,
        spoken_to=True,
        owed=False,
        launched=SimpleNamespace(epoch=old, conversation=SESSION),
    )
    calls = 0

    async def call(current, ref, method, params):
        nonlocal calls
        calls += 1
        if calls == 1:
            await seat.apply(seat.report(SessionExited(launch="a"), epoch=old))
            raise DeviceCallError("the runner's process exited before taking it")

    async def restart(ref, spec, access):
        running.launched = SimpleNamespace(
            epoch=await seat.register(), conversation=SESSION
        )

    host = SimpleNamespace(
        _session_factory=business_db_factory,
        _hand=None,
        _started=lambda ref: running,
        _call=call,
        start=restart,
        _wake=lambda current: None,
    )
    host._handing = lambda ref, current, input_id: SessionHost._handing(
        host, ref, current, input_id
    )

    await send(
        host,
        SessionRef(harness=HARNESS, home="h"),
        Prompt(identity.input_id, "perform once"),
        work_id=identity.work_id,
    )

    assert calls == 2
    assert await _held(seat, message) == (True, True), (
        "前一个进程的退出放开了已经交给新进程、还没读的那条输入"
    )


@pytest.mark.anyio
async def test_an_exit_stamped_by_a_clock_ahead_does_not_release_a_later_input(
    business_db_factory,
):
    """A 进程退出时它那台机器的时钟快了两秒，之后 B 起来，一条输入交给了 B：
    A 的退出不放开它。"""
    seat = await _seat(business_db_factory)
    old = await seat.register()
    await seat.apply(
        seat.report(
            SessionExited(launch="a"),
            epoch=old,
            at=datetime.now(UTC) + timedelta(seconds=2),
        )
    )
    await seat.register()
    _, message = await _registered(seat)

    assert await _held(seat, message) == (True, True)


@pytest.mark.anyio
async def test_an_input_whose_process_exited_no_longer_holds(business_db_factory):
    """交给哪个进程、哪个进程就退出了：再没有谁能读到它，座位放开，消息进下一轮。"""
    seat = await _seat(business_db_factory)
    epoch = await seat.register()
    _, message = await _registered(seat)
    assert await _held(seat, message) == (True, True)

    await seat.apply(seat.report(SessionExited(launch="a"), epoch=epoch))

    assert await _held(seat, message) == (False, False)


@pytest.mark.anyio
async def test_an_input_on_a_destroyed_machine_no_longer_holds(business_db_factory):
    """会话所在的机器被平台销毁、之后再没放到别的机器上：那条没读的输入再也读
    不到，座位放开。"""
    seat = await _seat(business_db_factory)
    _, message = await _registered(seat)
    async with seat.factory() as session:
        session.add(
            AgentSession(
                conversation_id=seat.topic,
                agent_handle="cheese",
                harness=HARNESS,
                resume_token=SESSION,
                execution_request={"sandbox_lost": True},
            )
        )
        await session.commit()

    assert await _held(seat, message) == (False, False)
