"""A runner the previous release started is drained and replaced, never read.

The deploy of a release that changes the runner's protocol finds the runners
the previous one started still alive: they live on the session host, not in
the backend. They answer ``ping`` without this release's lifecycle protocol and
refuse ``read``. An idle one is replaced at once: the old runner's screen is
closed and one of this release's is started on the same conversation, and
whatever the old one was written and never read is offered again. One at work
is left to finish: it is given nothing, a message for its seat waits, and it is
replaced once its ping says it is idle. Past the drain's cap its work is
interrupted, and the turn is started again by itself on the new runner.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app.core.config import settings
from app.core.device_failures import DeviceCallError
from app.core.sandbox_auth import mint_scoped_token
from app.domain.agent import initial_admission
from app.domain.agent.central_provider import CentralChannel
from app.domain.agent.device_provider import DeviceChannel
from app.domain.agent.harness.driven.runner import LONG_POLL
from app.domain.agent.models import AgentTurn, TurnResume
from app.domain.agent.room import sessions as room_sessions
from app.domain.agent.service import AgentResult
from app.domain.agent.session_host.contract import SessionError
from app.domain.agent.turn_resume import after_failure, hand_due
from app.domain.agent_instance.models import AgentInstance
from app.domain.block.models import (
    PROMPTED_TURN_META_KEY,
    Block,
    BlockKind,
    consumed_turn,
    prompted_turn,
)
from app.domain.delivery.input_holds import seat_has_unfinished_input
from app.domain.delivery.input_identity import InputEffects, InputIdentity
from app.domain.delivery.receipts import held_blocks, register_input
from app.domain.event_author import AuthorType
from app.domain.identity.handles import agent_instance_handle
from app.domain.topic_membership.services import TopicMemberService
from tests.integration.conftest import post_message
from tests.integration.test_a_release_restarts_quiet_sessions import (
    IDLE,
    ReleaseHub,
    _backend,
    _started_by_the_previous_release,
    _until,
    room,  # noqa: F401  fixture
)
from tests.integration.test_central_room_sessions import AGENT, ref, sessions
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
#: The work the old runner is in the middle of, as its ping says.
WORK = uuid.UUID("00000000-0000-4000-8000-0000000000aa")
AT_WORK = {**OLD_PING, "working": True, "work_id": str(WORK)}


class OldRunnerHub(ReleaseHub):
    """The session host while ``old``: the screen runs the previous release's
    runner. Closing the screen ends it; the screen opened next runs this
    release's. What either is told is kept, by which one was told it."""

    def __init__(self) -> None:
        super().__init__()
        self.old = False
        self.old_ping = dict(OLD_PING)
        self.interrupted = 0
        # What the interrupt fails with, when the connection to it breaks.
        self.interrupt_fails: Exception | None = None
        # What the work it interrupts had read by then.
        self.read_before_interrupt: list[str] = []
        # It accepts the interrupt and works on regardless.
        self.ignores_interrupt = False
        self.told: list[tuple[str, str]] = []

    async def call_executor(self, device_id, state, method, params, timeout=None):
        if device_id == "center" and method in ("send", "steer"):
            self.told.append(("old" if self.old else "new", params["text"]))
            return {"input_id": params["input_id"]}
        if self.old and device_id == "center":
            if method == "ping":
                return dict(self.old_ping)
            if method == "interrupt":
                if self.interrupt_fails is not None:
                    raise self.interrupt_fails
                # A runner that holds a registration refuses a call made
                # under any other (``lifecycle.StaleEpoch``).
                held = self.old_ping.get("worker_epoch")
                if held is not None and params.get("epoch") != held:
                    raise DeviceCallError(f"stale worker epoch: {params}")
                self.interrupted += 1
                if self.ignores_interrupt:
                    return {"interrupted": True}
                cut = {
                    "work": self.old_ping.get("work_id"),
                    "failure": {"kind": "interrupted"},
                    "inputs": list(self.read_before_interrupt),
                }
                self.old_ping = {
                    **self.old_ping,
                    "working": False,
                    "endings": [*self.old_ping.get("endings", []), cut],
                }
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
    return (await _written(client, session))[0]


async def _written(client, session) -> tuple[uuid.UUID, uuid.UUID]:
    """``_unread_input``'s message, and the platform's id of the input that
    carried it."""
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
    return block.id, input_id


async def _seated(client, project, topic) -> str:
    """Seat the room's agent on its roster; the handle it acts under."""
    async with client.test_request_factory() as db:
        instance = await db.scalar(
            select(AgentInstance).where(
                AgentInstance.project_id == project, AgentInstance.handle == AGENT
            )
        )
        assert instance is not None
        acting = agent_instance_handle(instance.id)
        await TopicMemberService(db).ensure_agent_seat(topic, acting)
        await db.commit()
    return acting


async def _prompted(client, session, work: uuid.UUID) -> uuid.UUID:
    """A person's message that started ``work``: fed to its prompt, which the
    transport accepted, and not yet read."""
    block = Block(
        id=uuid.uuid4(),
        project_id=session.project_id,
        conversation_id=session.conversation_id,
        kind=BlockKind.message,
        author_type=AuthorType.participant,
        author="alice",
        content="check the build",
        meta={"consumed_turn": None, PROMPTED_TURN_META_KEY: str(work)},
    )
    now = datetime.now(UTC)
    async with client.test_request_factory() as db:
        db.add(block)
        db.add(
            AgentTurn(
                id=work,
                conversation_id=session.conversation_id,
                continuation_id=work,
                author="alice",
                content="check the build",
                started_at=now,
                delivered_at=now,
            )
        )
        await db.commit()
    return block.id


async def _resumes(client, topic, work=WORK) -> list[TurnResume]:
    async with client.test_request_factory() as db:
        return list(
            await db.scalars(
                select(TurnResume)
                .where(
                    TurnResume.conversation_id == topic,
                    TurnResume.failed_turn_id == work,
                )
                .execution_options(populate_existing=True)
            )
        )


async def _said(client, topic) -> list[str]:
    async with client.test_request_factory() as db:
        blocks = await db.scalars(select(Block).where(Block.conversation_id == topic))
        return [str(block.content or "") for block in blocks]


@pytest.fixture
def center(monkeypatch):
    monkeypatch.setattr(settings, "agent_session_device_id", "center")
    # The drain asks the old runner often enough for a test to watch it.
    monkeypatch.setattr(room_sessions, "DRAIN_POLL_S", 0.05)


@pytest.mark.anyio
async def test_an_idle_old_runner_is_replaced_at_once(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    """Nobody writes to the room: its old runner is replaced all the same. The
    message it was written and never read is offered again; the one it read
    before the deploy finished is not."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    async def exercise():
        session = ref(project, topic)
        old = await _started_as_this_release_would(client, hub, tmp_path, session)
        block = await _unread_input(client, session)
        answered, answered_input = await _written(client, session)
        # The deploy: the runner on the host is the previous release's, idle
        # after a work that read one of the two.
        hub.old = True
        reader = uuid.uuid4()
        hub.old_ping = {
            **OLD_PING,
            "endings": [
                {
                    "work": str(reader),
                    "failure": None,
                    "inputs": [str(answered_input)],
                }
            ],
        }
        service = _backend(client, hub, tmp_path)
        await service.recover_sessions()

        await _until(lambda: old.sid in hub.closed and len(hub.opened) == 1)
        assert hub.opened[0].sid != old.sid
        async with client.test_request_factory() as db:
            assert not await seat_has_unfinished_input(db, topic, session.agent_handle)
            assert not await held_blocks(
                db,
                project_id=project,
                topic_id=topic,
                recipient_handle=session.agent_handle,
            )
            kept = await db.get(Block, block)
            assert kept is not None and consumed_turn(kept) is None
            read = await db.get(Block, answered)
            assert read is not None and str(consumed_turn(read)) == str(reader)
        assert hub.interrupted == 0
        await service.stop_listening(0)

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_a_working_old_runner_finishes_its_turn_and_is_replaced_once_idle(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    """The old runner was in the middle of a turn: it is not interrupted, what
    the agent does through the platform's tools lands, the turn ends as
    finished rather than failed, and the runner is replaced once it is idle."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    held: dict = {}

    async def deploy():
        session = ref(project, topic)
        held["acting"] = await _seated(client, project, topic)
        held["old"] = await _started_as_this_release_would(
            client, hub, tmp_path, session
        )
        # A message written to it just before the deploy, which it reads at its
        # next tool boundary.
        held["steered"], held["steered_input"] = await _written(client, session)
        hub.old = True
        hub.old_ping = dict(AT_WORK)
        held["service"] = service = _backend(client, hub, tmp_path)
        await service.recover_sessions()
        await asyncio.sleep(0.5)
        assert hub.interrupted == 0
        assert hub.closed == []
        assert service._compute.holds(topic, AGENT)

    client.portal.call(deploy)

    # The agent, still at work, answers in the room through its tools.
    token = mint_scoped_token(
        project_id=str(project), topic_id=str(topic), agent_handle=held["acting"]
    )
    answered = client.post(
        f"/topics/{topic}/messages",
        json={"request_id": str(uuid.uuid4()), "content": "the login page is fixed"},
        headers={"Authorization": f"Bearer {token}", "X-Cheese-Turn": str(WORK)},
    )
    assert answered.status_code == 200, answered.text

    async def finish():
        old = held["old"]
        await asyncio.sleep(0.3)
        assert hub.closed == [] and hub.interrupted == 0
        # A start that does not wait at admission (the platform's own work)
        # does not close the old runner's screen under its work.
        claude = held["service"]._compute._backends["claude-code"]
        with pytest.raises(SessionError):
            await claude.ensure(ref(project, topic), system_prompt="System")
        assert hub.closed == []
        # Its work is done.
        hub.old_ping = {
            **OLD_PING,
            "endings": [
                {
                    "work": str(WORK),
                    "failure": None,
                    "inputs": [str(held["steered_input"])],
                }
            ],
        }
        await _until(lambda: old.sid in hub.closed and len(hub.opened) == 1)
        assert hub.opened[0].sid != old.sid
        assert hub.interrupted == 0
        assert not held["service"]._compute.draining(topic, AGENT)
        await asyncio.sleep(0.3)
        assert await _resumes(client, topic) == []
        said = await _said(client, topic)
        assert "the login page is fixed" in said
        # What it read during the drain is not offered again.
        async with client.test_request_factory() as db:
            steered = await db.get(Block, held["steered"])
            assert steered is not None
            assert str(consumed_turn(steered)) == str(WORK)
            assert not await held_blocks(
                db, project_id=project, topic_id=topic, recipient_handle=AGENT
            )
        await held["service"].stop_listening(0)

    client.portal.call(finish)


async def _admitted(chat, topic_id, block_id) -> bool:
    """Whether a message to the seat starts its turn now (False: it waits)."""
    async with initial_admission.admitted_initial(
        chat.session_factory,
        chat.live.seat_lock_for,
        chat._turn_seat_handle,
        chat.post_system_event,
        topic_id,
        None,
        draining=chat,
        user_block_id=block_id,
        recipient_handle=AGENT,
    ) as waits:
        return not waits


@pytest.mark.anyio
async def test_a_message_sent_meanwhile_waits_and_reaches_the_new_runner(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    """A person writes while the old runner finishes: the message waits rather
    than going into the old runner, the room says so once, and the message goes
    to the runner of this release that replaces it."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    held: dict = {}

    async def deploy():
        session = ref(project, topic)
        held["old"] = await _started_as_this_release_would(
            client, hub, tmp_path, session
        )
        hub.old = True
        hub.old_ping = dict(AT_WORK)
        held["service"] = service = _backend(client, hub, tmp_path)
        await service.recover_sessions()

    client.portal.call(deploy)
    first = uuid.UUID(
        post_message(client, str(topic), "alice", {"content": "one"})["id"]
    )
    second = uuid.UUID(
        post_message(client, str(topic), "alice", {"content": "two"})["id"]
    )

    async def meanwhile_and_after():
        service, old = held["service"], held["old"]
        assert not await _admitted(service, topic, first)
        assert not await _admitted(service, topic, second)
        waiting = [line for line in await _said(client, topic) if "平台更新中" in line]
        assert len(waiting) == 1, "told once per drain, not once per message"
        async with client.test_request_factory() as db:
            kept = await db.get(Block, first)
            assert kept is not None and consumed_turn(kept) is None
        assert hub.told == []

        hub.old_ping = dict(OLD_PING)
        await _until(lambda: old.sid in hub.closed)
        assert await _admitted(service, topic, first)
        claude = service._compute._backends["claude-code"]
        await claude.send(
            ref(project, topic),
            "one",
            system_prompt="System",
            work_id=uuid.uuid4(),
            on_mark=lambda _: None,
            register_input=AsyncMock(),
        )
        assert [whom for whom, _ in hub.told] == ["new"]
        assert hub.told[0][1].endswith("one")
        await service.stop_listening(0)

    client.portal.call(meanwhile_and_after)


@pytest.mark.anyio
@pytest.mark.parametrize("was_a_resume", [False, True])
async def test_past_the_cap_the_old_runners_turn_is_interrupted_and_resumed(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    monkeypatch,
    was_a_resume,
):
    """The old runner is still at work when the drain's cap runs out: its work
    is taken away under the registration it holds, and the turn is started
    again by itself. The upgrade cut it short, not the agent failing, so it is
    resumed even when that turn was itself the resume of a failure a moment
    before (``was_a_resume``)."""
    monkeypatch.setattr(settings, "runner_drain_s", 3.0)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

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
        steered = None
        if not was_a_resume:
            # A message steered to it before the deploy, which the work reads
            # at a tool boundary before it is told to stop. (A person's message
            # read ends a resume streak, so not in the case that keeps one.)
            steered, steered_input = await _written(client, session)
            hub.read_before_interrupt = [str(steered_input)]
        hub.old = True
        # A runner of a later protocol holds a registration of its own.
        hub.old_ping = {**AT_WORK, "worker_epoch": 7}
        service = _backend(client, hub, tmp_path)

        await service.recover_sessions()
        await asyncio.sleep(0.4)
        # Within the cap the work goes on.
        assert hub.interrupted == 0
        assert await _resumes(client, topic) == []

        await _until(lambda: hub.interrupted == 1)
        found: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (found := await _resumes(client, topic)):
                await asyncio.sleep(0.05)
        await service.stop_listening(0)
        (resume,) = found
        assert resume.reason == "runner_replaced"
        assert resume.dropped_at is None, resume.dropped_reason
        # The streak carries on as it stood: the upgrade is not one more
        # failure for the agent's next resume to count against.
        assert resume.attempt == (1 if was_a_resume else 0)
        # What it read before it stopped is not offered again.
        if steered is not None:
            async with client.test_request_factory() as db:
                read = await db.get(Block, steered)
                assert read is not None and consumed_turn(read) is not None

    client.portal.call(exercise)


async def _drain_one(client, hub, tmp_path, project, topic, *, before=None):
    """A deploy that finds the room's old runner at work; the backend.
    ``before`` sets up what was there before the deploy."""
    await _seated(client, project, topic)
    await _started_as_this_release_would(client, hub, tmp_path, ref(project, topic))
    if before is not None:
        await before()
    hub.old = True
    hub.old_ping = {**AT_WORK, "worker_epoch": 7}
    service = _backend(client, hub, tmp_path)
    await service.recover_sessions()
    return service


@pytest.mark.anyio
async def test_work_the_old_runner_ends_in_a_failure_is_resumed(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
):
    """The old runner went idle on its own, but its work failed (the model
    service refused it), and it then took in a message it had been written in
    a work of its own: the turn is started again on the new runner, not closed
    as finished. The message it took in is not offered again; one it never
    read is."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    session = ref(project, topic)
    later = uuid.uuid4()
    written: dict = {}

    async def before_the_deploy():
        # Two messages written to it: it reads one in a work of its own after
        # the failed work, and never reads the other.
        written["taken"], written["taken_input"] = await _written(client, session)
        written["block"] = await _unread_input(client, session)
        # And the message the failed work was started with, which it failed
        # before reading.
        written["prompt"] = await _prompted(client, session, WORK)

    async def exercise():
        service = await _drain_one(
            client, hub, tmp_path, project, topic, before=before_the_deploy
        )
        taken, taken_input = written["taken"], written["taken_input"]
        block, prompt = written["block"], written["prompt"]
        old = hub.opened[0]
        hub.old_ping = {
            **OLD_PING,
            "worker_epoch": 7,
            "endings": [
                {
                    "work": str(WORK),
                    "failure": {"kind": "api_error", "status": 529},
                    "inputs": [],
                },
                {
                    "work": str(later),
                    "failure": None,
                    "inputs": [str(taken_input)],
                },
            ],
        }
        rows: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (rows := await _resumes(client, topic)):
                await asyncio.sleep(0.05)
        # The resume is written as the failed turn is closed, before what that
        # turn read is settled; the old screen closes once all of it is.
        await _until(lambda: old.sid in hub.closed)
        await service.stop_listening(0)
        assert [row.reason for row in rows] == ["runner_replaced"]
        assert hub.interrupted == 0
        # What it never read is offered again, not taken as read.
        async with client.test_request_factory() as db:
            kept = await db.get(Block, block)
            assert kept is not None and consumed_turn(kept) is None
            read = await db.get(Block, taken)
            # Read, by the work that read it.
            assert read is not None and str(consumed_turn(read)) == str(later)
            # The failed work did not hear its own prompt: the message is no
            # longer counted as that work's.
            opening = await db.get(Block, prompt)
            assert opening is not None and prompted_turn(opening) is None
            assert consumed_turn(opening) is None
            assert not await held_blocks(
                db, project_id=project, topic_id=topic, recipient_handle=AGENT
            )

    client.portal.call(exercise)


@pytest.mark.anyio
@pytest.mark.parametrize("taken_away_by", ["closing_the_task", "removing_the_agent"])
async def test_taking_the_work_away_stops_the_old_runners_work_for_good(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    taken_away_by,
):
    """Work taken away while the old runner drains (the task closed, the agent
    removed) reaches that runner under the registration it holds, and ends
    there: it is not started again."""
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    async def exercise():
        service = await _drain_one(client, hub, tmp_path, project, topic)
        old = hub.opened[0]
        if taken_away_by == "closing_the_task":
            # The closed-task sweep finds it at work.
            assert topic in service._compute.working_conversations()
            await service._compute.stop_work(topic)
        else:
            await service._compute.dismiss(topic, AGENT)
        assert hub.interrupted == 1
        await _until(lambda: old.sid in hub.closed)
        await asyncio.sleep(0.3)
        await service.stop_listening(0)
        assert await _resumes(client, topic) == []

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_an_interrupt_that_cannot_be_delivered_still_ends_the_drain(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    monkeypatch,
):
    """Past the cap the connection to the old runner breaks as it is told to
    stop: the seat is not left waiting on it, and the turn is started again."""
    monkeypatch.setattr(settings, "runner_drain_s", 0.3)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    hub.interrupt_fails = httpx.RemoteProtocolError("owner connection reset")

    async def exercise():
        service = await _drain_one(client, hub, tmp_path, project, topic)
        rows: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (rows := await _resumes(client, topic)):
                await asyncio.sleep(0.05)
        assert not service._compute.draining(topic, AGENT)
        await service.stop_listening(0)
        assert [row.reason for row in rows] == ["runner_replaced"]

    client.portal.call(exercise)


@pytest.mark.anyio
async def test_the_cap_counts_from_when_the_drain_began_across_backends(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    monkeypatch,
):
    """A backend takes the seat over in the middle of a drain: it goes on
    counting the cap from when the drain began, not from when it found the
    runner, so a chain of deploys cannot keep an old runner draining for ever."""
    monkeypatch.setattr(settings, "runner_drain_s", 4.0)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    async def first():
        first_backend = await _drain_one(client, hub, tmp_path, project, topic)
        await asyncio.sleep(3.0)
        assert hub.interrupted == 0
        # The next deploy: this backend lets go of the seat.
        await first_backend.stop_listening(0)

    async def second():
        second_backend = _backend(client, hub, tmp_path)
        await second_backend.recover_sessions()
        # Its own count would reach the cap 4 s from now; the drain's reaches
        # it within about 1 s.
        async with asyncio.timeout(2.5):
            while hub.interrupted == 0:
                await asyncio.sleep(0.05)
        await second_backend.stop_listening(0)

    client.portal.call(first)
    client.portal.call(second)


@pytest.mark.anyio
async def test_a_drain_left_by_another_runner_does_not_count_for_this_one(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    monkeypatch,
):
    """A drain cut off by a deploy whose runner then went away leaves its start
    on the seat. A later runner of another release found at work there gets
    its own full drain, not what is left of that one."""
    monkeypatch.setattr(settings, "runner_drain_s", 2.0)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)

    async def first_runner():
        first = _backend(client, hub, tmp_path)
        await _seated(client, project, topic)
        await _started_as_this_release_would(client, hub, tmp_path, ref(project, topic))
        hub.old = True
        hub.old_ping = {**AT_WORK, "launch": "first"}
        await first.recover_sessions()
        await asyncio.sleep(0.3)
        await first.stop_listening(0)
        # It goes away during the deploy, and the next backend finds it gone.
        hub.old_ping = {**OLD_PING, "alive": False}
        between = _backend(client, hub, tmp_path)
        await between.recover_sessions()
        await between.stop_listening(0)
        await asyncio.sleep(2.0)

    async def second_runner():
        # Past the first drain's cap, another old runner is found at work.
        hub.old_ping = {**AT_WORK, "launch": "second"}
        later = _backend(client, hub, tmp_path)
        await later.recover_sessions()
        await asyncio.sleep(1.0)
        # Still draining: neither interrupted nor replaced under its work.
        assert hub.interrupted == 0
        assert later._compute.draining(topic, AGENT)
        assert await _resumes(client, topic) == []
        await later.stop_listening(0)

    client.portal.call(first_runner)
    client.portal.call(second_runner)


@pytest.mark.anyio
async def test_a_runner_that_works_on_after_the_interrupt_is_replaced_anyway(
    client,
    room,  # noqa: F811
    center,
    tmp_path,
    monkeypatch,
):
    """Past the cap the old runner accepts the interrupt but keeps working: the
    drain waits a bounded while for it to say how the work ended, then settles
    without it, so the seat's messages are not held for good."""
    monkeypatch.setattr(settings, "runner_drain_s", 0.3)
    monkeypatch.setattr(room_sessions, "DRAIN_STOP_WAIT_S", 0.5)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
    hub.ignores_interrupt = True

    async def exercise():
        service = await _drain_one(client, hub, tmp_path, project, topic)
        rows: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (rows := await _resumes(client, topic)):
                await asyncio.sleep(0.05)
        assert not service._compute.draining(topic, AGENT)
        await service.stop_listening(0)
        assert hub.interrupted == 1
        assert [row.reason for row in rows] == ["runner_replaced"]

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
        old = await _started_as_this_release_would(client, hub, tmp_path, session)
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
            # The seat is settled once its old runner has been replaced.
            await _until(lambda: old.sid in hub.closed)
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
    monkeypatch,
):
    """The seat was waiting out a rate limit hours long; a person told it to
    carry on, which ends that wait, and the turn that started is cut short by
    the upgrade's cap. It is resumed within seconds, not after the limit."""
    monkeypatch.setattr(settings, "runner_drain_s", 0.5)
    project, topic = room
    hub = OldRunnerHub()
    hub.ping = dict(IDLE)
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
        hub.old_ping = dict(AT_WORK)
        service = _backend(client, hub, tmp_path)
        await service.recover_sessions()
        await _until(lambda: hub.interrupted == 1)

        rows: list[TurnResume] = []
        async with asyncio.timeout(HANG_S):
            while not (rows := await _resumes(client, topic)):
                await asyncio.sleep(0.05)
        await service.stop_listening(0)
        (row,) = rows
        now = datetime.now(UTC)
        assert row.reason == "runner_replaced"
        assert row.due_at <= now + timedelta(seconds=10)
        assert await hand_due(factory, now=now + timedelta(seconds=60)) == 1

    client.portal.call(exercise)
