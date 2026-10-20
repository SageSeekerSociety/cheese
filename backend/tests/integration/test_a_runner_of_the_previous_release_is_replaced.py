"""A runner the previous release started is replaced, not read.

The deploy of a release that changes the runner's protocol finds the runners
the previous one started still alive: they live on the session host, not in
the backend. They answer ``ping`` without the lifecycle protocol and refuse
``read``. Their seats must take messages again at once: the next start closes
the old runner's screen and starts one of this release's on the same
conversation, and a turn the old runner was in the middle of ends with the
upgrade as its failure, which the platform starts again by itself.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.device_failures import DeviceCallError
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.models import TurnResume
from app.domain.agent.service import AgentResult
from app.domain.agent.turn_resume import after_failure, hand_due
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import Block, BlockKind, consumed_turn
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.receipts import held_blocks, register_input
from app.domain.event_author import AuthorType
from tests.integration.test_a_release_restarts_quiet_sessions import (
    IDLE,
    ReleaseHub,
    _backend,
    _started_by_the_previous_release,
    _until,
    room,  # noqa: F401  fixture
)
from tests.integration.test_central_room_sessions import ref, sessions
from tests.support.hang import HANG_S

#: How a runner of the previous release answers: no lifecycle protocol.
OLD_PING = {
    "alive": True,
    "working": False,
    "tasks": {},
    "session_id": "s",
    "capabilities": [LONG_POLL],
    "input_protocol": 1,
}


class OldRunnerHub(ReleaseHub):
    """The session host while ``old``: the screen runs the previous release's
    runner. Closing the screen ends it; the screen opened next runs this
    release's."""

    def __init__(self) -> None:
        super().__init__()
        self.old = False
        self.old_ping = dict(OLD_PING)
        self.interrupted = 0

    async def call_executor(self, device_id, state, method, params, timeout=None):
        if self.old and device_id == "center":
            if method == "ping":
                return dict(self.old_ping)
            if method == "interrupt":
                self.interrupted += 1
                return {"interrupted": True}
            if method == "read":
                raise DeviceCallError("Unknown Claude Code session operation: read")
        return await super().call_executor(device_id, state, method, params)

    async def close_screen(self, device_id, sid) -> bool:
        self.old = False
        return await super().close_screen(device_id, sid)


async def _started_as_this_release_would(client, hub, tmp_path, session):
    """The session's screen, opened with everything this release would open it
    with: only the runner in it is the previous release's, so nothing but its
    protocol says it must be replaced."""
    previous = sessions(
        CentralChannel(
            DeviceChannel(hub=hub, session_factory=client.test_request_factory)
        )
    )
    previous.channel._device_api_base = AsyncMock(return_value="http://central-api")
    await previous.ensure(session, system_prompt="System")
    (screen,) = hub.opened
    return screen


async def _unread_input(client, session) -> uuid.UUID:
    """A message the session's runner was written and has not read, under the
    native session the old runner answers for."""
    block = Block(
        id=uuid.uuid4(),
        project_id=session.project_id,
        conversation_id=session.conversation_id,
        kind=BlockKind.message,
        author_type=AuthorType.participant,
        author="alice",
        content="fix the login page",
        meta={"consumed_turn": None},
    )
    input_id = uuid.uuid4()
    identity = InputIdentity(
        session.project_id,
        session.conversation_id,
        session.agent_handle,
        "claude-code",
        OLD_PING["session_id"],
        input_id,
        input_id,
    )
    async with client.test_request_factory() as db:
        db.add(block)
        await db.flush()
        await register_input(db, identity, InputEffects(held_block_ids=(block.id,)))
        await db.commit()
    return block.id


@pytest.fixture
def center(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")


@pytest.mark.anyio
async def test_a_seat_whose_old_runner_survived_the_deploy_takes_messages_at_once(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    async def exercise():
        session = ref(project, topic)
        old = await _started_as_this_release_would(client, hub, tmp_path, session)
        # A message the old runner was written and never read.
        block = await _unread_input(client, session)
        # The deploy: the runner on the host is the previous release's.
        hub.old = True
        service = _backend(client, hub, tmp_path)
        await service.recover_sessions()
        claude = service._compute._backends["claude-code"]

        async def holds() -> tuple[bool, set]:
            async with client.test_request_factory() as db:
                return (
                    await seat_has_unfinished_input(db, topic, session.agent_handle),
                    await held_blocks(
                        db,
                        project_id=project,
                        topic_id=topic,
                        recipient_handle=session.agent_handle,
                    ),
                )

        # The seat is free for the next message, and the unread one is
        # offered again rather than swallowed.
        async with asyncio.timeout(HANG_S):
            while await holds() != (False, set()):
                await asyncio.sleep(0.05)
        async with client.test_request_factory() as db:
            kept = await db.get(Block, block)
            assert kept is not None and consumed_turn(kept) is None

        await claude.ensure(session, system_prompt="System")

        assert old.sid in hub.closed
        assert [s.sid for s in hub.opened] != [old.sid] and len(hub.opened) == 1
        assert (topic, session.agent_handle) in claude.live
        await service.stop_listening(0)

    client.portal.call(exercise)


@pytest.mark.anyio
@pytest.mark.parametrize("was_a_resume", [False, True])
async def test_a_turn_the_old_runner_was_in_is_interrupted_and_resumed(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    was_a_resume,
):
    """The old runner was in the middle of a turn: its work is taken away, and
    the turn is started again by itself. The upgrade cut it short, not the
    agent failing, so it is resumed even when that turn was itself the
    resume of a failure a moment before (``was_a_resume``)."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    work = uuid.uuid4()

    async def exercise():
        session = ref(project, topic)
        await _started_by_the_previous_release(client, hub, tmp_path, session)
        if was_a_resume:
            # The turn running at deploy is the resume of a rate-limited
            # turn, handed to the seat a moment before.
            now = datetime.now(UTC)
            async with client.test_request_factory() as db:
                instance = await db.scalar(
                    select(AgentInstance).where(
                        AgentInstance.project_id == project,
                        AgentInstance.is_active.is_(True),
                        AgentInstance.handle != "cheese",
                    )
                )
                assert instance is not None
                db.add(
                    TurnResume(
                        id=uuid.uuid4(),
                        project_id=project,
                        conversation_id=topic,
                        recipient_handle=hub.opened[0].agent_handle,
                        agent_instance_id=instance.id,
                        failed_turn_id=uuid.uuid4(),
                        reason="rate_limited",
                        said="429",
                        attempt=1,
                        failed_at=now,
                        due_at=now,
                        handed_at=now,
                    )
                )
                await db.commit()
        hub.old = True
        hub.old_ping = {**OLD_PING, "working": True, "work_id": str(work)}
        service = _backend(client, hub, tmp_path)

        await service.recover_sessions()

        async def resume_of_the_turn() -> list[TurnResume]:
            async with client.test_request_factory() as db:
                return list(
                    await db.scalars(
                        select(TurnResume).where(
                            TurnResume.conversation_id == topic,
                            TurnResume.failed_turn_id == work,
                        )
                    )
                )

        await _until(lambda: hub.interrupted == 1)
        found: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (found := await resume_of_the_turn()):
                await asyncio.sleep(0.05)
        await service.stop_listening(0)
        (resume,) = found
        assert resume.reason == "runner_replaced"
        assert resume.dropped_at is None, resume.dropped_reason
        # The streak carries on as it stood: the upgrade is not one more
        # failure for the agent's next resume to count against.
        assert resume.attempt == (1 if was_a_resume else 0)

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_replacement_that_failed_once_is_settled_on_the_retry(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    monkeypatch,
):
    """Settling the replaced runner fails once (the database was away for a
    moment): recovery tries the seat again, and this time what the old runner
    never read is dropped and the seat is free."""
    from app.domain.agent import recovery, runner_reports

    monkeypatch.setattr(recovery, "REPLAY_RETRY_S", 0.1)
    real = runner_reports.drop_unread_of_session
    calls = {"n": 0}

    async def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("database away for a moment")
        return await real(*args, **kwargs)

    monkeypatch.setattr(runner_reports, "drop_unread_of_session", flaky)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    async def exercise():
        session = ref(project, topic)
        await _started_as_this_release_would(client, hub, tmp_path, session)
        await _unread_input(client, session)
        hub.old = True
        service = _backend(client, hub, tmp_path)
        await service.recover_sessions()

        async def holds():
            async with client.test_request_factory() as db:
                return (
                    await seat_has_unfinished_input(db, topic, session.agent_handle),
                    await held_blocks(
                        db,
                        project_id=project,
                        topic_id=topic,
                        recipient_handle=session.agent_handle,
                    ),
                )

        try:
            async with asyncio.timeout(HANG_S):
                while await holds() != (False, set()):
                    await asyncio.sleep(0.05)
        finally:
            await service.stop_listening(0)
        assert calls["n"] >= 2

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_turn_a_person_restarted_is_resumed_at_once_after_the_upgrade(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    """The seat was waiting out a rate limit hours long; a person told it to
    carry on, which ends that wait, and the turn that started is cut short by
    the upgrade. It is resumed within seconds, not after the limit."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    work = uuid.uuid4()
    factory = client.test_request_factory
    limited = (
        "API Error: Server is temporarily limiting requests (not your usage limit)"
        " · Rate limited. Please try again in 4 hours"
    )

    async def exercise():
        session = ref(project, topic)
        await _started_by_the_previous_release(client, hub, tmp_path, session)
        seat = hub.opened[0].agent_handle
        waiting = await after_failure(
            factory,
            project_id=project,
            conversation_id=topic,
            seat=seat,
            failed_turn_id=uuid.uuid4(),
            result=AgentResult(text=limited, session_id="s", is_error=True),
            route="subscription",
        )
        assert waiting is not None and waiting.dropped_at is None
        await asyncio.sleep(0.05)
        async with factory() as db:
            db.add(
                Block(
                    id=uuid.uuid4(),
                    project_id=project,
                    conversation_id=topic,
                    kind=BlockKind.message,
                    author_type=AuthorType.participant,
                    author="alice",
                    content="fixed it, carry on",
                    meta={},
                )
            )
            await db.commit()
        await asyncio.sleep(0.05)
        hub.old = True
        hub.old_ping = {**OLD_PING, "working": True, "work_id": str(work)}
        service = _backend(client, hub, tmp_path)
        await service.recover_sessions()
        await _until(lambda: hub.interrupted == 1)

        async def resume_of_the_turn() -> TurnResume | None:
            async with factory() as db:
                return await db.scalar(
                    select(TurnResume)
                    .where(
                        TurnResume.conversation_id == topic,
                        TurnResume.failed_turn_id == work,
                    )
                    .execution_options(populate_existing=True)
                )

        row: TurnResume | None = None
        async with asyncio.timeout(HANG_S):
            while (row := await resume_of_the_turn()) is None:
                await asyncio.sleep(0.05)
        await service.stop_listening(0)
        now = datetime.now(UTC)
        assert row.reason == "runner_replaced"
        assert row.due_at <= now + timedelta(seconds=10)
        assert await hand_due(factory, now=now + timedelta(seconds=60)) == 1

    client.portal.call(exercise)
