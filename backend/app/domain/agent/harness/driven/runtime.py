"""Drive durable sessions and land their records in the shared room log.

The runtime does not hold the turn. The session lives in a runner on the session
machine that outlives this process; the runtime reads it from a cursor, one
poller per room, and ``recover`` finds it again after a restart. The iterator
``run_turn`` hands back reads that same session.

A harness supplies its handle, its subscription, and the three verbs its
protocol spells differently: what a runner's ``ping`` says about a turn in
flight, what words said mid-turn are sent as, and how a turn is taken away.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Protocol

import httpx

from app.domain.agent.device_hub import DeviceCallError, DeviceOffline
from app.domain.agent.harness import (
    ActivityConsumer,
    Backlog,
    EventConsumer,
    Opening,
    ReceiptConsumer,
    SessionRef,
    UnreadProbe,
)
from app.domain.agent.harness.driven.subscription import Subscription
from app.domain.agent.service import AgentEvent, AgentResult, AgentSessionInfo

# Every read of a room's journal is a call to its device, and an idle room
# answers it with nothing. Read at the floor while there is anything to read;
# let the wait grow towards the ceiling once the journal has gone quiet.
READ_FLOOR_S = 0.1
READ_CEILING_S = 1.0


class Handle(Protocol):
    """Where one room's session runs, as the channel found it."""

    @property
    def session(self) -> SessionRef: ...

    @property
    def device_id(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def agent_handle(self) -> str: ...

    @property
    def mirror(self) -> Path: ...


class SessionChannel[H: Handle](Protocol):
    name: str
    provisions_machine: bool
    deferred_work: bool
    builds_model_env: bool

    def available(self) -> bool: ...

    async def prepare_topic(self, **kwargs) -> tuple[bool, str]: ...

    async def ensure(self, session: SessionRef, opening: Opening) -> H: ...

    async def call(self, handle: H, method: str, params: dict) -> dict: ...

    async def discover(self, device_id: str | None) -> list[H]: ...

    async def images(self, handle: H, images: list[dict]) -> list: ...


class DrivenRuntime[H: Handle]:
    harness: str
    embeds_images = True
    #: How messages a person may read name the harness.
    label: str
    #: How the poller's log lines name what it reads.
    records: str
    #: The poller's exception line, which alerts are already grouped by.
    read_failure: str
    #: The harness module's own logger, so a line names where it came from.
    logger: logging.Logger
    #: The runner method that takes words said to a session mid-turn.
    steer: str

    def __init__(self, channel: SessionChannel[H], *, hard_ceiling_s: float = 900):
        self.channel = channel
        self.hard_ceiling_s = hard_ceiling_s
        self.live: dict[uuid.UUID, H] = {}
        self.subscriptions: dict[uuid.UUID, Subscription] = {}
        self.tasks: dict[uuid.UUID, asyncio.Task] = {}
        self.work: dict[uuid.UUID, uuid.UUID] = {}
        self.queues: dict[uuid.UUID, asyncio.Queue[AgentEvent]] = {}
        self.woken: dict[uuid.UUID, asyncio.Event] = {}
        self.consumer: EventConsumer | None = None
        self.activity: ActivityConsumer | None = None
        self.receipts: ReceiptConsumer | None = None

    # --- what the harness supplies -------------------------------------------

    def conversation(self, handle: H) -> str:
        """The harness's own id for the session the handle points at."""
        raise NotImplementedError

    def subscribe(
        self, handle: H, call: Callable[[str, dict], Awaitable[dict]]
    ) -> Subscription:
        raise NotImplementedError

    def backlog(self, session: SessionRef) -> Backlog:
        raise NotImplementedError

    def working(self, status: dict) -> bool:
        """Whether a runner's ``ping`` answer says a turn is in flight."""
        raise NotImplementedError

    async def interrupt(self, session: SessionRef) -> bool:
        raise NotImplementedError

    # --- the runtime surface -------------------------------------------------

    @property
    def name(self) -> str:
        return self.channel.name

    @property
    def provisions_machine(self) -> bool:
        return self.channel.provisions_machine

    @property
    def deferred_work(self) -> bool:
        return self.channel.deferred_work

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
        # The runner answers a send once the session has taken the input, so
        # there is no input sitting unread whose age anyone would ask about.
        pass

    async def _consume(self, project, topic, work, event, eid, seen, unsolicited):
        if queue := self.queues.get(work):
            await queue.put(event)
        elif self.consumer:
            await self.consumer(project, topic, work, event, eid, seen, unsolicited)
        else:
            raise RuntimeError(f"{self.label} room persistence is not bound")

    async def _activity(self, project, topic, work, active):
        if active:
            self.work[topic] = work
        elif self.work.get(topic) == work:
            self.work.pop(topic, None)
        if self.activity and work not in self.queues:
            await self.activity(project, topic, work, active)

    async def _attach(self, handle: H) -> None:
        topic = handle.session.topic_id
        if self.live.get(topic) == handle and topic in self.subscriptions:
            return
        await self._detach(topic)
        handle.mirror.parent.mkdir(parents=True, exist_ok=True)

        async def call(method: str, params: dict) -> dict:
            return await self.channel.call(handle, method, params)

        self.live[topic] = handle
        self.subscriptions[topic] = self.subscribe(handle, call)

    def _listen(self, topic: uuid.UUID) -> None:
        if topic not in self.tasks or self.tasks[topic].done():
            self.tasks[topic] = asyncio.create_task(
                self._poll(topic), name=f"{self.records} {topic}"
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
                # a name that described a fault.
                if not waiting:
                    waiting = True
                    self.logger.warning(
                        "%s waiting for the device topic=%s", self.records, topic
                    )
                await asyncio.sleep(2)
            except DeviceCallError as exc:
                # The machine is there and said no — the runner's socket is not
                # up yet (a cold one can take about a minute) or its home is gone.
                # Either way the next read is what tells, and the machine's
                # own words are the fact worth writing down, once.
                if not waiting:
                    waiting = True
                    self.logger.warning(
                        "%s waiting for the runner topic=%s: %s",
                        self.records,
                        topic,
                        exc,
                    )
                await asyncio.sleep(2)
            except httpx.TransportError as exc:
                # The connection owner is being replaced, or the socket to it
                # went while this read was in flight. Retrying is what this loop
                # is for, and the owner is back within seconds — but at ERROR
                # every release of it wrote a read failure into the alert
                # channel, as it did at 01:47 UTC on 2026-09-20.
                if not waiting:
                    waiting = True
                    self.logger.warning(
                        "%s waiting for the connection owner topic=%s: %s",
                        self.records,
                        topic,
                        exc,
                    )
                await asyncio.sleep(2)
            except Exception:
                # The runner outlives a backend or connector outage. Re-reading
                # is safe because the landing cursor only moves after the
                # persistence callback returned.
                self.logger.exception("%s topic=%s", self.read_failure, topic)
                await asyncio.sleep(2)
            else:
                if waiting:
                    waiting = False
                    self.logger.info("%s resumed topic=%s", self.records, topic)
                # A room being worked reads at the floor, and so does one whose
                # journal just gave us something — the next record of a stream
                # is due immediately. A room nobody is talking to costs a call
                # every 100ms for an empty page, and the cost is per room:
                # eleven of them idling held a core between them. Sending wakes
                # the wait, so nothing a person does is served at the
                # backed-off rate.
                if delivered or topic in self.work:
                    delay = READ_FLOOR_S
                else:
                    delay = min(delay * 2, READ_CEILING_S)
                await self._wait(topic, delay)

    async def _died(self, handle: H) -> None:
        """The process is gone with a turn open. Say so where the turn is, or
        the room waits on something that will never answer."""
        topic = handle.session.topic_id
        work = self.work[topic]
        await self._consume(
            handle.session.project_id,
            topic,
            work,
            AgentResult(
                text=f"{self.label} session process exited",
                session_id=self.conversation(handle),
                is_error=True,
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"{self.harness}:{self.conversation(handle)}:exit:{work}",
            False,
            False,
        )
        await self._activity(handle.session.project_id, topic, work, False)
        self.live.pop(topic, None)

    async def ensure(self, session, opening, *, work_id=None) -> H:
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
                session_id=self.conversation(handle),
                agent_handle=handle.agent_handle,
                harness=self.harness,
            ),
            f"{self.harness}:{self.conversation(handle)}:opening:{work_id}",
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

    async def deliver(
        self, topic_id, text, images=None, *, expected_work_id=None
    ) -> bool:
        """A person talking to a session that is already working."""
        handle = self.live.get(topic_id)
        work = self.work.get(topic_id)
        if handle is None or work is None:
            return False
        if expected_work_id is not None and work != expected_work_id:
            return False
        status = await self.channel.call(handle, "ping", {})
        if not self.working(status):
            return False
        if self.live.get(topic_id) is not handle or self.work.get(topic_id) != work:
            return False
        await self.channel.call(
            handle,
            self.steer,
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
                self.logger.warning(
                    "%s recovery failed topic=%s device=%s: %s",
                    self.label,
                    handle.session.topic_id,
                    handle.device_id,
                    exc,
                )
                continue
            await self._attach(handle)
            if self.working(status) and status.get("work_id"):
                self.work[handle.session.topic_id] = uuid.UUID(status["work_id"])
            recovered.append(handle.session)
        # Chat restores room bookkeeping before replay starts consumption.
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
        session_agent: str,
    ) -> AsyncIterator[AgentEvent]:
        if topic_id is None:
            yield AgentResult(
                text=f"A {self.label} session requires a room",
                session_id=None,
                is_error=True,
            )
            return
        work = turn_id or uuid.uuid4()
        queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.queues[work] = queue
        try:
            await self.send(
                SessionRef(project_id, topic_id, session_agent, harness=self.harness),
                prompt,
                Opening(
                    system_prompt,
                    resume_session_id,
                    model,
                    env,
                    memory_scope,
                    owner,
                    agent_handle,
                    # 平台自己起的那几轮走这条入口，今天照旧租手：它们跑在项目工
                    # 作机的根话题沙箱里。写出来是为了让这条老路看得见，改不改是
                    # 另一件事 (P21 只动房间里的那一轮)。
                    needs_place=True,
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
