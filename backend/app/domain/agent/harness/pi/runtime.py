"""Drive durable pi sessions and land their entries in the shared room log.

The four verbs map onto pi without translation, which is most of why this
harness is worth having: ``send`` is ``prompt``, a person interrupting with
words mid-turn is ``steer``, taking the work away is ``abort``, and reading from
a cursor is ``get_entries since=``. Nothing here has to reconstruct a boundary
the harness did not report.

What it does NOT do is hold the turn. The iterator ``run_turn`` hands back reads
a session that outlives it; a room's turn does not go through here at all. The
runner keeps working while this process is replaced, and ``recover`` finds it
again.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import (
    ActivityConsumer,
    EventConsumer,
    Opening,
    ReceiptConsumer,
    SessionRef,
    UnreadProbe,
)
from app.domain.agent.harness.pi.backlog import PiBacklog
from app.domain.agent.harness.pi.subscription import Subscription
from app.domain.agent.service import AgentEvent, AgentResult, AgentSessionInfo

logger = logging.getLogger(__name__)

PI = "pi"

# Every read of a room's entry log is a call to its device, and an idle room
# answers it with nothing. Read at the floor while there is anything to read;
# let the wait grow towards the ceiling once the log has gone quiet.
READ_FLOOR_S = 0.1
READ_CEILING_S = 1.0


@dataclass(frozen=True)
class Handle:
    session: SessionRef
    device_id: str
    state: str
    session_id: str
    agent_handle: str
    mirror: Path


class SessionChannel(Protocol):
    name: str
    provisions_machine: bool
    builds_model_env: bool

    def available(self) -> bool: ...

    async def prepare_topic(self, **kwargs) -> tuple[bool, str]: ...

    async def ensure(self, session: SessionRef, opening: Opening) -> Handle: ...

    async def call(self, handle: Handle, method: str, params: dict) -> dict: ...

    async def discover(self, device_id: str | None) -> list[Handle]: ...

    async def images(self, handle: Handle, images: list[dict]) -> list[dict]: ...


class PiRuntime:
    harness = PI
    embeds_images = True

    def __init__(self, channel: SessionChannel, *, hard_ceiling_s: float = 900):
        self.channel = channel
        self.hard_ceiling_s = hard_ceiling_s
        self.live: dict[uuid.UUID, Handle] = {}
        self.subscriptions: dict[uuid.UUID, Subscription] = {}
        self.tasks: dict[uuid.UUID, asyncio.Task] = {}
        self.work: dict[uuid.UUID, uuid.UUID] = {}
        self.queues: dict[uuid.UUID, asyncio.Queue[AgentEvent]] = {}
        self.woken: dict[uuid.UUID, asyncio.Event] = {}
        self.consumer: EventConsumer | None = None
        self.activity: ActivityConsumer | None = None
        self.receipts: ReceiptConsumer | None = None

    @property
    def name(self) -> str:
        return self.channel.name

    @property
    def provisions_machine(self) -> bool:
        return self.channel.provisions_machine

    @property
    def builds_model_env(self) -> bool:
        return self.channel.builds_model_env

    def available(self) -> bool:
        return self.channel.available()

    async def prepare_topic(self, **kwargs) -> tuple[bool, str]:
        return await self.channel.prepare_topic(**kwargs)

    def bind_events(self, consumer: EventConsumer) -> None:
        self.consumer = consumer

    def bind_activity(self, consumer: ActivityConsumer) -> None:
        self.activity = consumer

    def bind_receipts(self, consumer: ReceiptConsumer) -> None:
        self.receipts = consumer

    def bind_unread_probe(self, probe: UnreadProbe) -> None:
        # ``prompt`` answers once the session has taken the message, so there is
        # no input sitting unread whose age anyone would have to ask about.
        pass

    async def _consume(self, project, topic, work, event, eid, seen, unsolicited):
        if queue := self.queues.get(work):
            await queue.put(event)
        elif self.consumer:
            await self.consumer(project, topic, work, event, eid, seen, unsolicited)
        else:
            raise RuntimeError("pi room persistence is not bound")

    async def _activity(self, project, topic, work, active):
        if active:
            self.work[topic] = work
        elif self.work.get(topic) == work:
            self.work.pop(topic, None)
        if self.activity and work not in self.queues:
            await self.activity(project, topic, work, active)

    async def _attach(self, handle: Handle) -> None:
        topic = handle.session.topic_id
        if self.live.get(topic) == handle and topic in self.subscriptions:
            return
        await self._detach(topic)
        handle.mirror.parent.mkdir(parents=True, exist_ok=True)

        async def call(method: str, params: dict) -> dict:
            return await self.channel.call(handle, method, params)

        self.live[topic] = handle
        self.subscriptions[topic] = Subscription(
            handle.session,
            handle.mirror,
            call,
            self._consume,
            self._activity,
            handle.session_id,
        )

    def _listen(self, topic: uuid.UUID) -> None:
        if topic not in self.tasks or self.tasks[topic].done():
            self.tasks[topic] = asyncio.create_task(
                self._poll(topic), name=f"pi entries {topic}"
            )

    def _wake(self, topic: uuid.UUID) -> None:
        """Cut short the wait of a room that has just been given something."""
        if event := self.woken.get(topic):
            event.set()

    async def _wait(self, topic: uuid.UUID, delay: float) -> None:
        event = self.woken.setdefault(topic, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), delay)
        except TimeoutError:
            return
        event.clear()

    async def _poll(self, topic: uuid.UUID) -> None:
        checked_at = 0.0
        # Waiting for a device to come back is this loop's job, not a failure of
        # it, so the wait is said once and the return is said once. Per-task
        # state: one of these runs per topic.
        waiting = False
        delay = READ_FLOOR_S
        while topic in self.subscriptions:
            try:
                delivered = await self.subscriptions[topic].drain()
                if topic in self.work and time.monotonic() - checked_at >= 1:
                    handle = self.live[topic]
                    status = await self.channel.call(handle, "ping", {})
                    checked_at = time.monotonic()
                    if not status.get("alive", True):
                        await self._died(handle)
                        return
            except DeviceOffline:
                # A room whose machine is switched off is the ordinary state of
                # a platform nobody is using this minute, and this loop exists
                # to wait it out. Logged as an exception it was two ERROR lines
                # a second per topic, every one of them an alert: 37 of the 53
                # messages in the alert channel on 2026-09-16 were this, under
                # a name that described a fault. Only #1114 made it nameable —
                # before that the offline device arrived as a bare
                # `HTTPStatusError` and could not be told apart from a real one.
                if not waiting:
                    waiting = True
                    logger.warning("pi entries waiting for the device topic=%s", topic)
                await asyncio.sleep(2)
            except DeviceCallError as exc:
                # The machine is there and said no — the runner's socket is not
                # up yet (a cold pi takes about a minute) or its home is gone.
                # Either way the next read is what tells, and the machine's
                # own words are the fact worth writing down, once.
                if not waiting:
                    waiting = True
                    logger.warning(
                        "pi entries waiting for the runner topic=%s: %s", topic, exc
                    )
                await asyncio.sleep(2)
            except Exception:
                # The runner outlives a backend or connector outage. Re-reading
                # is safe because the landing cursor only moves after the
                # persistence callback returned.
                logger.exception("pi entry read failed topic=%s", topic)
                await asyncio.sleep(2)
            else:
                if waiting:
                    waiting = False
                    logger.info("pi entries resumed topic=%s", topic)
                # A room being worked reads at the floor, and so does one whose
                # log just gave us something — the next entry of a stream is due
                # immediately. A room nobody is talking to costs a call every
                # 100ms for an empty page, and the cost is per room: eleven of
                # them idling held a core between them. Sending wakes the wait,
                # so nothing a person does is served at the backed-off rate.
                if delivered or topic in self.work:
                    delay = READ_FLOOR_S
                else:
                    delay = min(delay * 2, READ_CEILING_S)
                await self._wait(topic, delay)

    async def _died(self, handle: Handle) -> None:
        """The process is gone with a turn open. Say so where the turn is, or
        the room waits on something that will never answer."""
        topic = handle.session.topic_id
        work = self.work[topic]
        await self._consume(
            handle.session.project_id,
            topic,
            work,
            AgentResult(
                text="pi session process exited",
                session_id=handle.session_id,
                is_error=True,
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"pi:{handle.session_id}:exit:{work}",
            False,
            False,
        )
        await self._activity(handle.session.project_id, topic, work, False)
        self.live.pop(topic, None)

    async def ensure(self, session, opening, *, work_id=None) -> Handle:
        previous = self.live.get(session.topic_id)
        if previous and opening.agent_handle != previous.agent_handle:
            # A different teammate is taking the room; the conversation that
            # belonged to the last one does not carry over to them.
            await self.interrupt(session)
            await self.close(session)
        handle = await self.channel.ensure(session, opening)
        await self._attach(handle)
        return handle

    async def send(
        self,
        session: SessionRef,
        message: str,
        opening: Opening,
        *,
        work_id: uuid.UUID,
        on_mark: Callable[[uuid.UUID], None],
        images: list[dict] | None = None,
    ) -> bool:
        handle = await self.ensure(session, opening, work_id=work_id)
        payload = await self.channel.images(handle, images or [])
        on_mark(work_id)
        await self._consume(
            session.project_id,
            session.topic_id,
            work_id,
            AgentSessionInfo(
                session_id=handle.session_id,
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"pi:{handle.session_id}:opening:{work_id}",
            False,
            False,
        )
        self.work[session.topic_id] = work_id
        self._wake(session.topic_id)
        try:
            await self.channel.call(
                handle,
                "send",
                {
                    "input_id": str(work_id),
                    "work_id": str(work_id),
                    "text": message,
                    "images": payload,
                },
            )
            if self.receipts:
                await self.receipts(session.topic_id, message)
        finally:
            # A lost acknowledgement does not mean the session stopped working.
            self._listen(session.topic_id)
        return True

    async def deliver(self, topic_id, text, images=None) -> bool:
        """A person talking to a session that is already working: ``steer``.

        pi delivers it after the current tool calls finish and before the next
        model call, which is the earliest point at which saying something can
        still change what happens.
        """
        handle = self.live.get(topic_id)
        work = self.work.get(topic_id)
        if handle is None or work is None:
            return False
        status = await self.channel.call(handle, "ping", {})
        if not status.get("working"):
            return False
        await self.channel.call(
            handle,
            "steer",
            {
                "input_id": str(uuid.uuid4()),
                "work_id": str(work),
                "text": text,
                "images": await self.channel.images(handle, images or []),
            },
        )
        if self.receipts:
            await self.receipts(topic_id, text)
        return True

    def holds(self, topic_id) -> bool:
        return topic_id in self.live

    def backlog(self, session: SessionRef) -> PiBacklog:
        handle = self.live.get(session.topic_id)
        return PiBacklog(
            handle.mirror if handle else None,
            handle.session_id if handle else None,
        )

    async def interrupt(self, session: SessionRef) -> bool:
        handle = self.live.get(session.topic_id)
        if handle is None:
            return False
        return bool((await self.channel.call(handle, "abort", {}))["aborted"])

    async def _detach(self, topic: uuid.UUID) -> None:
        task = self.tasks.pop(topic, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.subscriptions.pop(topic, None)
        self.live.pop(topic, None)
        self.work.pop(topic, None)
        self.woken.pop(topic, None)

    async def close(self, session: SessionRef) -> None:
        if subscription := self.subscriptions.get(session.topic_id):
            await subscription.drain()
        await self._detach(session.topic_id)

    async def recover(self, device_id=None) -> list[SessionRef]:
        handles = await self.channel.discover(device_id)
        recovered = []
        for handle in handles:
            try:
                async with asyncio.timeout(15):
                    status = await self.channel.call(handle, "ping", {})
            except (DeviceOffline, DeviceCallError, TimeoutError) as exc:
                logger.warning(
                    "pi recovery failed topic=%s device=%s: %s",
                    handle.session.topic_id,
                    handle.device_id,
                    exc,
                )
                continue
            await self._attach(handle)
            if status.get("working") and status.get("work_id"):
                self.work[handle.session.topic_id] = uuid.UUID(status["work_id"])
            recovered.append(handle.session)
        return recovered

    async def replay(self, session: SessionRef, *, known_texts: set[str]) -> None:
        if subscription := self.subscriptions.get(session.topic_id):
            await subscription.drain()
            self._listen(session.topic_id)

    async def run_turn(
        self,
        *,
        project_id,
        topic_id,
        prompt,
        system_prompt,
        resume_session_id,
        model=None,
        env=None,
        memory_scope=None,
        owner=None,
        turn_id=None,
        images=None,
        agent_handle=None,
    ) -> AsyncIterator[AgentEvent]:
        if topic_id is None:
            yield AgentResult(
                text="A pi session requires a room", session_id=None, is_error=True
            )
            return
        work = turn_id or uuid.uuid4()
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.queues[work] = queue
        try:
            await self.send(
                SessionRef(project_id, topic_id),
                prompt,
                Opening(
                    system_prompt,
                    resume_session_id,
                    model,
                    env,
                    memory_scope,
                    owner,
                    agent_handle,
                ),
                work_id=work,
                on_mark=lambda _: None,
                images=images,
            )
            while True:
                event = await queue.get()
                yield event
                if isinstance(event, AgentResult):
                    return
        finally:
            self.queues.pop(work, None)
