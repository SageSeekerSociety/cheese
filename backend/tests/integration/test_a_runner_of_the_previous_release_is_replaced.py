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
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.device_failures import DeviceCallError
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.models import TurnResume
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
async def test_a_turn_the_old_runner_was_in_is_interrupted_and_resumed(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    work = uuid.uuid4()

    async def exercise():
        session = ref(project, topic)
        await _started_by_the_previous_release(client, hub, tmp_path, session)
        hub.old = True
        hub.old_ping = {**OLD_PING, "working": True, "work_id": str(work)}
        service = _backend(client, hub, tmp_path)

        await service.recover_sessions()

        async def resumed() -> list[TurnResume]:
            async with client.test_request_factory() as db:
                return list(
                    await db.scalars(
                        select(TurnResume).where(TurnResume.conversation_id == topic)
                    )
                )

        await _until(lambda: hub.interrupted == 1)
        found: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (found := await resumed()):
                await asyncio.sleep(0.05)
        assert found, "the turn the upgrade cut short was not resumed"
        await service.stop_listening(0)

    client.portal.call(exercise)
