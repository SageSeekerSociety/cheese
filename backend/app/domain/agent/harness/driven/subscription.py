"""Hand a mirrored journal to the room's persistence, and say when it is working.

A drain pulls whatever the runner has that the mirror does not, then walks the
unread tail in order. Every record the runner hands over is stamped with the
work it was produced under — the stamp is the runner's because only it knows
what was in flight when the record appeared, and a backend that came up after
the fact would have to guess. The landing cursor moves only after the room took
a record, so a drain that dies halfway re-reads rather than skips.

What a harness supplies is what its protocol decides: how to pull from its
runner, how to read its mirror, which records open and close a turn, and what to
do with a record produced before the first input.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import (
    ActivityConsumer,
    Backlog,
    EventConsumer,
    HarnessEvent,
    SessionRef,
)
from app.domain.agent.service import AgentResult


class Subscription[B: Backlog]:
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: ActivityConsumer,
    ):
        self.session, self.path, self.call = session, path, call
        self.consume, self.activity = consume, activity
        self.lock = asyncio.Lock()

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

    async def drain(self) -> int:
        async with self.lock:
            await self.receive()
            reader = self.reader()
            delivered = 0
            for entry in reader.unread():
                assert isinstance(entry.record, dict)
                record = entry.record
                stamp = (record.get("cheese") or {}).get("work_id")
                if stamp is None:
                    self.unowned(entry, reader)
                else:
                    work_id = uuid.UUID(stamp)
                    events = reader.assemble(entry)
                    if self.starts_turn(record, reader):
                        await self.activity(
                            self.session.project_id,
                            self.session.topic_id,
                            work_id,
                            True,
                        )
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
                            False,
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
            return delivered
