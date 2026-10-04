"""Hand a mirrored journal to the room's persistence, and say when it is working.

A drain pulls whatever the runner has that the mirror does not, then walks the
unread tail in order, a page at a time. Every record the runner hands over is
stamped with the work it was produced under — the stamp is the runner's because
only it knows what was in flight when the record appeared, and a backend that
came up after the fact would have to guess. The landing cursor moves only after
the room took a record, so a drain that dies halfway re-reads rather than skips.
It moves once per page, and where the room refused a record partway through
one: a commit per record is a sync per record, and a backlog can run to a
million records.

A record that waited too long to be read is stepped over, not landed
(``STALE_S``): the cursor moves past it and the room never hears it. One that
ends a turn still ends it, and the turns of what was read inside it, without a
word to the room: nothing else would, while the session goes on answering. So
is one the room went on refusing (``REFUSED_TIMES``), with its id in the error
log.

What a harness supplies is what its protocol decides: how to pull from its
runner, how to read its mirror, which records open and close a turn, what to do
with a record produced before the first input, and — where the harness reports
it — which record says an input was read.

The mirror is a sqlite file that commits synchronously, and every room on a
backend shares one event loop, so no read or write of the mirror runs on that
loop: every landing is a transaction, and on a slow disk each of those syncs
would stall every room this backend serves. They run on the mirror's own
thread (``on_disk``) instead. One thread per mirror is what keeps the mirror's
semantics: sqlite connections stay on the thread that opened them, and the
writes happen one at a time, in the order the drain asked for them, each
awaited before the next is asked for.
"""

import asyncio
import dataclasses
import functools
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from app.domain.agent.harness import (
    Backlog,
    EventConsumer,
    HarnessEvent,
    SessionRef,
)
from app.domain.agent.service import (
    AgentEvent,
    AgentMessage,
    AgentResult,
    AgentStepFailed,
    AgentToolResult,
    AgentToolUse,
)
from app.domain.delivery.input_identity import (
    CompletionConsumer,
    InputReceipt,
    ReceiptConsumer,
    TerminationConsumer,
    WorkCompletion,
    WorkTermination,
)

#: What a record says about a working session, for the liveness rules
#: (``RoomSessions.verdict``): it said something, work moved. A tool starting
#: or coming back is ``started(call)`` / ``returned(call)``, by the harness's id
#: for the call, so tools that run side by side are counted one by one.
OUTPUT, PROGRESS = "output", "progress"
TOOL_STARTED, TOOL_RETURNED = "tool_started:", "tool_returned:"

#: 一间房里的一个座位：(topic, agent_handle)。一间房坐着几个 agent，就有几
#: 条会话；runtime 那侧的会话、订阅、在跑的活和它的钟都按座位键住，不再按房间
#: —— ``SessionRef`` 的文档一直说 (topic, agent_handle, harness) 才命名一条会话，
#: 这里兑现它。
Seat = tuple[uuid.UUID, str]

#: What one record of a turn's work said about how it is going (``marks``).
Moved = Callable[[uuid.UUID, frozenset[str]], Awaitable[None]]

#: 一轮开/关的回报，按座位而不是按房间：同一间房里另一个 agent 的一轮开开关关，
#: 不碰这个座位的「在跑的活」和它的钟。
SeatActivity = Callable[[uuid.UUID, Seat, uuid.UUID, bool], Awaitable[None]]

#: How long a landed record stays in the mirror, and how often that is looked
#: at. The mirror is not only the queue a room is fed from: it is the raw record
#: of what a session did, and the first thing anyone reaches for when a block
#: looks wrong. A day answers that and keeps a busy room's mirror small.
RETENTION_S = 24 * 3600
RETENTION_EVERY_S = 3600

#: How old an unlanded record may be and still reach the room. A record gets
#: this old only while nothing read the journal: the backend was gone, the
#: machine was, or every drain stopped at a record it could not take. By then
#: the room has gone on without it — its prompt re-sent or the person told — so
#: landing it would answer, hours late, what has been answered since, and open
#: the books again for turns the session started by itself. A record that ends
#: a turn is the exception in part: the turn is still open, and a session that
#: still answers is one the orphan sweep leaves alone, so it ends the turn
#: (``_end_late``) and lands nothing. Two hours is the orphan sweep's own line
#: between a deploy and an outage (``ORPHAN_STALE_S``): past it, the platform
#: stops acting for the person on its own.
#:
#: Age is by the time the journal gives a record: when the session machine
#: recorded it for Claude Code and Codex, when the backend mirrored it for pi.
#: So for pi, output mirrored late after the backend itself was away is fresh.
STALE_S = 2 * 3600

#: When a record the room keeps refusing is stepped over: after this many
#: drains refused it, the first at least this long ago. Every drain starts at
#: the record that failed, so one the room can never take stops the session's
#: output reaching the room for good, while the session goes on working and its
#: journal grows behind it. Both bounds, because a record refused only while
#: the database was away is one the room takes once it is back.
REFUSED_TIMES = 3
REFUSED_OVER_S = 10 * 60

logger = logging.getLogger(__name__)


def started(call: str) -> str:
    return TOOL_STARTED + call


def returned(call: str) -> str:
    return TOOL_RETURNED + call


def marks_of(events: list[AgentEvent]) -> set[str]:
    """What the room's own vocabulary says about how a turn is going."""
    marks: set[str] = set()
    for event in events:
        if isinstance(event, AgentMessage):
            marks.add(OUTPUT)
        elif isinstance(event, AgentToolUse):
            marks.add(PROGRESS)
            if event.call_id:
                marks.add(started(event.call_id))
        elif isinstance(event, AgentStepFailed):
            marks |= {PROGRESS, returned(event.call_id)}
        elif isinstance(event, AgentToolResult | AgentResult):
            marks.add(PROGRESS)
    return marks


class Subscription[B: Backlog]:
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: SeatActivity,
        *,
        receipts: ReceiptConsumer | None = None,
        completions: CompletionConsumer | None = None,
        terminations: TerminationConsumer | None = None,
        moved: Moved | None = None,
    ):
        self.session, self.path, self.call = session, path, call
        self.consume, self.activity = consume, activity
        self.receipts = receipts
        self.completions = completions
        self.terminations = terminations
        self.moved = moved
        self.lock = asyncio.Lock()
        self.forgotten_at = 0.0
        self.disk = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"mirror {session.topic_id}"
        )
        # What the first read of the drain under way asks besides the cursor,
        # what that read answered besides records, and that read while the
        # runner holds it: it has nothing in hand yet, so a drain that wants
        # to land now has it give way (``unpark``) rather than wait it out.
        self.asking: dict | None = None
        self.heard: dict = {}
        self.parked: asyncio.Future | None = None
        # A drain that wants to land now and found nothing held yet: the drain
        # holding the lock may still be on its way to that read, and parking it
        # then would keep this one waiting for the whole hold.
        self.hurry = False
        # The mark of what the agent was last seen writing.
        self.live_mark: str | None = None

    @property
    def seat(self) -> Seat:
        """This subscription's own seat: (topic, agent) of the session it reads."""
        return (self.session.topic_id, self.session.agent_handle)

    async def on_disk[T](self, work: Callable[..., T], /, *args, **kwargs) -> T:
        """Run ``work`` on the mirror's thread, after whatever was asked before."""
        return await asyncio.get_running_loop().run_in_executor(
            self.disk, functools.partial(work, *args, **kwargs)
        )

    async def release(self) -> None:
        """Let the mirror go once the write it may be in the middle of is done.

        A drain cancelled while it waits on the mirror leaves that write running
        on the mirror's thread; whoever opens the file next must start after it,
        not beside it.
        """
        await asyncio.to_thread(self.disk.shutdown)

    async def receive(self) -> None:
        """Pull whatever the runner has that the mirror does not, touching the
        mirror only through ``on_disk``, a page at a time through ``read``."""
        raise NotImplementedError

    def unpark(self) -> None:
        """Have a read the runner is holding read at once instead."""
        self.hurry = True
        if self.parked is not None:
            self.parked.cancel()

    async def read(self, method: str, params: dict) -> dict:
        """One page from the runner. The first page of a drain that may wait
        asks the runner to hold it until there is news (``driven.runner``),
        and what else that answer says is kept in ``heard``."""
        asking, self.asking = self.asking, None
        if asking is None:
            return await self.call(method, params)
        if self.hurry:
            # Asked to land now before this read was held: read at once, this
            # once; the next drain holds its read again.
            self.hurry = False
            return await self.call(method, params)
        self.parked = asyncio.ensure_future(self.call(method, {**params, **asking}))
        try:
            answer = await self.parked
        except asyncio.CancelledError:
            task = asyncio.current_task()
            if task is not None and task.cancelling():
                raise
            # Given way (``unpark``): read now instead.
            return await self.call(method, params)
        finally:
            self.parked = None
        self.heard = {k: v for k, v in answer.items() if k in ("alive", "live")}
        if live := self.heard.get("live"):
            self.live_mark = live.get("mark")
        return answer

    def reader(self) -> B:
        raise NotImplementedError

    def starts_turn(self, record: dict, reader: B) -> bool:
        raise NotImplementedError

    def ends_turn(self, record: dict, reader: B) -> bool:
        raise NotImplementedError

    def unowned(self, entry: HarnessEvent, reader: B) -> None:
        """A record from before the first input, which no turn owns."""
        raise NotImplementedError

    def receipt(self, record: dict) -> InputReceipt | None:
        """Native evidence for a specific input, distinct from RPC acceptance."""
        return None

    def completion(self, record: dict) -> WorkCompletion | None:
        return None

    def termination(self, record: dict) -> WorkTermination | None:
        """The work interval this record says ended without completing.

        Only a harness can answer this: whether a result is an error, or was
        interrupted, and which inputs that work owned, are its facts. The
        default knows nothing and frees nothing.
        """
        return None

    async def reconcile_history(self) -> None:
        """A harness may reconcile retained facts behind its landing cursor."""

    async def settle_termination(self, record: dict) -> WorkTermination | None:
        termination = self.termination(record)
        if termination is not None:
            if self.terminations is None:
                raise RuntimeError("Termination consumer is not bound")
            await self.terminations(termination)
        return termination

    async def settle_completion(self, record: dict) -> WorkCompletion | None:
        # A terminated work is not a completion, and a record settles at most
        # one of the two. Termination goes first so an error or a Stop can never
        # fall through to the completion consumer.
        if await self.settle_termination(record) is not None:
            return None
        completion = self.completion(record)
        if completion is not None:
            if self.completions is None:
                raise RuntimeError("Completion consumer is not bound")
            await self.completions(completion)
        return completion

    def marks(self, record: dict, events: list[AgentEvent]) -> set[str]:
        """What this record says about the turn. The events answer most of it;
        a harness adds what its records say and the vocabulary does not (a tool
        coming back with nothing to show)."""
        return marks_of(events)

    async def drain(self, wait: float = 0.0) -> int:
        """Land what the runner has past the cursor; how many events that was.

        ``wait`` has the runner hold the first read up to that long until there
        is something to answer it with.
        """
        if wait <= 0:
            self.unpark()
        async with self.lock:
            self.heard = {}
            if wait > 0:
                self.asking = {"wait": wait, "live": self.live_mark}
            else:
                self.hurry = False
            try:
                await self.receive()
            finally:
                self.asking = None
            reader = await self.on_disk(self.reader)
            delivered = stale = 0
            # A backlog nobody read for hours is mostly records too old to
            # land. The run of them at the cursor is stepped over in one move:
            # read a page at a time, a day of a busy session's journal took
            # longer than the backend it was recovered by stayed up.
            if not reader.unfinished():
                stale = await self.on_disk(reader.step_over_older, than_s=STALE_S)
            # How far the room has taken whole things; landed once per page.
            whole: str | None = None
            try:
                while page := await self.on_disk(reader.unread):
                    for entry in page:
                        assert isinstance(entry.record, dict)
                        receipt = self.receipt(entry.record)
                        if receipt is not None:
                            # Settlement is never subject to output-age or poison
                            # record skipping. Failure leaves this cursor replayable.
                            if self.receipts is None:
                                raise RuntimeError("Receipt consumer is not bound")
                            await self.receipts(receipt)
                        # Settlement precedes age/poison handling even when the
                        # runner is idle and this process never saw the start.
                        completion = await self.settle_completion(entry.record)
                        if entry.age_s >= STALE_S:
                            stale += 1
                            if self.ends_turn(entry.record, reader):
                                await self._end_late(entry, reader)
                        else:
                            try:
                                delivered += await self._deliver(entry, reader)
                            except Exception:
                                if completion is not None:
                                    raise
                                if not await self.on_disk(
                                    reader.refused,
                                    entry.key,
                                    times=REFUSED_TIMES,
                                    over_s=REFUSED_OVER_S,
                                ):
                                    raise
                                logger.exception(
                                    "stepped over journal record %s the room "
                                    "refused %d times over %ds topic=%s agent=%s",
                                    entry.eid,
                                    REFUSED_TIMES,
                                    REFUSED_OVER_S,
                                    self.session.topic_id,
                                    self.session.agent_handle,
                                )
                        if not reader.unfinished():
                            whole = entry.key
                    if whole is not None:
                        await self.on_disk(reader.landed, through=whole)
                        whole = None
            except Exception:
                # The room refused a record partway through a page: what it
                # took before that stays taken, and the next drain starts at
                # the refusal. A cancelled drain does not wait on its disk for
                # this; the next one re-reads the page and the ids absorb it.
                if whole is not None:
                    await self.on_disk(reader.landed, through=whole)
                raise
            finally:
                if stale:
                    logger.warning(
                        "stepped over %d journal records older than %ds "
                        "topic=%s agent=%s",
                        stale,
                        STALE_S,
                        self.session.topic_id,
                        self.session.agent_handle,
                    )
            if time.monotonic() - self.forgotten_at >= RETENTION_EVERY_S:
                self.forgotten_at = time.monotonic()
                await self.on_disk(reader.forget, older_than_s=RETENTION_S)
            return delivered

    async def _end_late(self, entry: HarnessEvent, reader: B) -> None:
        """End the turn a record too old to land closes, saying nothing.

        The ending is the same as a fresh one's — the turn's interval closed,
        the turns of inputs read inside it ended with its outcome — but neither
        its closing text nor its failure reaches the room.
        """
        assert isinstance(entry.record, dict)
        work = (entry.record.get("cheese") or {}).get("work_id")
        if work is None:
            return
        work_id = uuid.UUID(work)
        for event in reader.assemble(entry):
            if not isinstance(event, AgentResult) or event.thread_label is not None:
                continue
            await self.consume(
                self.session.project_id,
                self.session.topic_id,
                work_id,
                dataclasses.replace(
                    event,
                    text="",
                    late=True,
                    agent_handle=event.agent_handle or self.session.agent_handle,
                ),
                getattr(event, "eid", None) or entry.eid,
                False,
                False,
            )
        await self.activity(self.session.project_id, self.seat, work_id, False)

    async def _deliver(self, entry: HarnessEvent, reader: B) -> int:
        """Hand one record to the room; how many events it came out as."""
        assert isinstance(entry.record, dict)
        delivered = 0
        record = entry.record
        stamp = record.get("cheese") or {}
        work = stamp.get("work_id")
        if work is None:
            self.unowned(entry, reader)
        else:
            work_id = uuid.UUID(work)
            events = reader.assemble(entry)
            if self.starts_turn(record, reader):
                await self.activity(
                    self.session.project_id,
                    self.seat,
                    work_id,
                    True,
                )
            # The input receipt is not emitted here: the drain loop emits it
            # before the age/poison checks, so that a settlement never rides on
            # a record the room may step over. Emitting it here as well would
            # deliver the same receipt twice.
            if self.moved is not None:
                await self.moved(work_id, frozenset(self.marks(record, list(events))))
            for event in events:
                # Which seat's session produced this event. Events that
                # declare the field keep what the record said (the
                # runner's stamp is authoritative); the rest carry this
                # subscription's own seat, so a consumer opening the
                # books for a self-started turn attributes it to the
                # right agent when several seats share one room — the
                # room-keyed fallback cannot tell them apart.
                fields = getattr(type(event), "__dataclass_fields__", None)
                if fields is not None and "agent_handle" not in fields:
                    # Dynamic by design: the stamp lands on events whose
                    # dataclass never heard of it, which is exactly what
                    # the check above proved.
                    cast(Any, event).agent_handle = self.session.agent_handle
                await self.consume(
                    self.session.project_id,
                    self.session.topic_id,
                    work_id,
                    event,
                    getattr(event, "eid", None) or entry.eid,
                    # The closing text was already landed as its own
                    # message; the result must not publish it twice.
                    isinstance(event, AgentResult) and not event.is_error,
                    # A turn the session started for itself, which the
                    # room has to open the books for when it speaks.
                    bool(stamp.get("unsolicited")),
                )
                delivered += 1
            if self.ends_turn(record, reader):
                await self.activity(
                    self.session.project_id,
                    self.seat,
                    work_id,
                    False,
                )
        return delivered
