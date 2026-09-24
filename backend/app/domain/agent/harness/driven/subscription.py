"""Hand a mirrored journal to the room's persistence, and say when it is working.

A drain pulls whatever the runner has that the mirror does not, then walks the
unread tail in order. Every record the runner hands over is stamped with the
work it was produced under — the stamp is the runner's because only it knows
what was in flight when the record appeared, and a backend that came up after
the fact would have to guess. The landing cursor moves only after the room took
a record, so a drain that dies halfway re-reads rather than skips.

What a harness supplies is what its protocol decides: how to pull from its
runner, how to read its mirror, which records open and close a turn, what to do
with a record produced before the first input, and — where the harness reports
it — which record says an input was read.
"""

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import (
    ActivityConsumer,
    Backlog,
    EventConsumer,
    HarnessEvent,
    ReceiptConsumer,
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

#: What a record says about a working session, for the liveness rules
#: (``DrivenRuntime.verdict``): it said something, work moved. A tool starting
#: or coming back is ``started(call)`` / ``returned(call)``, by the harness's id
#: for the call, so tools that run side by side are counted one by one.
OUTPUT, PROGRESS = "output", "progress"
TOOL_STARTED, TOOL_RETURNED = "tool_started:", "tool_returned:"

Pulse = Callable[[uuid.UUID, frozenset[str]], None]

#: How long a landed record stays in the mirror, and how often that is looked
#: at. The mirror is not only the queue a room is fed from: it is the raw record
#: of what a session did, and the first thing anyone reaches for when a block
#: looks wrong. A day answers that and keeps a busy room's mirror small.
RETENTION_S = 24 * 3600
RETENTION_EVERY_S = 3600


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
        activity: ActivityConsumer,
        *,
        receipts: ReceiptConsumer | None = None,
        pulse: Pulse | None = None,
    ):
        self.session, self.path, self.call = session, path, call
        self.consume, self.activity = consume, activity
        self.receipts, self.pulse = receipts, pulse
        self.lock = asyncio.Lock()
        self.forgotten_at = 0.0

    async def receive(self) -> None:
        """Pull whatever the runner has that the mirror does not."""
        raise NotImplementedError

    def reader(self) -> B:
        raise NotImplementedError

    def starts_turn(self, record: dict, reader: B) -> bool:
        raise NotImplementedError

    def ends_turn(self, record: dict, reader: B) -> bool:
        raise NotImplementedError

    def unowned(self, entry: HarnessEvent, reader: B) -> None:
        """A record from before the first input, which no turn owns."""
        raise NotImplementedError

    def receipt(self, record: dict) -> str | None:
        """The text of an input this record says the session read, if it is
        such a record. A harness that takes an input the moment it is written
        has nothing to report here: ``DrivenRuntime`` reports those on send."""
        return None

    def marks(self, record: dict, events: list[AgentEvent]) -> set[str]:
        """What this record says about the turn. The events answer most of it;
        a harness adds what its records say and the vocabulary does not (a tool
        coming back with nothing to show)."""
        return marks_of(events)

    async def drain(self) -> int:
        async with self.lock:
            await self.receive()
            reader = self.reader()
            delivered = 0
            for entry in reader.unread():
                assert isinstance(entry.record, dict)
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
                            self.session.topic_id,
                            work_id,
                            True,
                        )
                    if self.pulse is not None:
                        self.pulse(
                            self.session.topic_id,
                            frozenset(self.marks(record, list(events))),
                        )
                    text = self.receipt(record)
                    if text is not None and self.receipts is not None:
                        await self.receipts(self.session.topic_id, text)
                    for event in events:
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
                            self.session.topic_id,
                            work_id,
                            False,
                        )
                if not reader.unfinished():
                    reader.landed(through=entry.key)
            if time.monotonic() - self.forgotten_at >= RETENTION_EVERY_S:
                self.forgotten_at = time.monotonic()
                reader.forget(older_than_s=RETENTION_S)
            return delivered
