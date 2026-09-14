"""Hand mirrored pi entries to the room's persistence, and say when it is working.

Activity comes out of the entry log itself rather than a separate signal: a turn
begins at the thing a person said and ends at the assistant message that stopped
for a reason other than a tool call. Nothing else has to be trusted to report
it, which matters because the report would have to survive the same restart the
log already survives.

Every entry the runner hands over is stamped with the work it was produced
under. The stamp is the runner's because only it knows what was in flight when
the entry appeared — a backend that came up after the fact would have to guess,
and a wrong guess files a message under the wrong turn.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import ActivityConsumer, EventConsumer, SessionRef
from app.domain.agent.harness.pi.backlog import PiBacklog
from app.domain.agent.harness.pi.events import CONTINUES
from app.domain.agent.harness.pi.journal import PAGE, Journal
from app.domain.agent.service import AgentResult


async def receive(path: Path, call: Callable[[str, dict], Awaitable[dict]]) -> None:
    """Pull whatever the runner has that we do not, a page at a time."""
    journal = Journal(path)
    try:
        since = journal.recall("received")
        while True:
            page = (await call("entries", {"since": since}))["entries"]
            journal.import_entries(page)
            if len(page) < PAGE:
                return
            since = page[-1]["id"]
    finally:
        journal.close()


def _role(entry: dict) -> str:
    return (entry.get("message") or {}).get("role", "")


def _ends_the_turn(entry: dict) -> bool:
    message = entry.get("message") or {}
    return message.get("role") == "assistant" and message.get("stopReason") != CONTINUES


class Subscription:
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: ActivityConsumer,
        session_id: str | None = None,
    ):
        self.session, self.path, self.call = session, path, call
        self.consume, self.activity = consume, activity
        self.session_id = session_id
        self.lock = asyncio.Lock()

    async def drain(self) -> int:
        async with self.lock:
            await receive(self.path, self.call)
            reader = PiBacklog(self.path, self.session_id)
            delivered = 0
            for entry in reader.unread():
                assert isinstance(entry.record, dict)
                record = entry.record
                stamp = (record.get("cheese") or {}).get("work_id")
                if stamp is None:
                    # Everything before the first input belongs to no turn:
                    # the model and thinking-level entries pi writes at startup.
                    reader.landed(through=entry.key)
                    continue
                work_id = uuid.UUID(stamp)
                if _role(record) == "user":
                    await self.activity(
                        self.session.project_id, self.session.topic_id, work_id, True
                    )
                for event in reader.assemble(entry):
                    await self.consume(
                        self.session.project_id,
                        self.session.topic_id,
                        work_id,
                        event,
                        getattr(event, "eid", None) or entry.eid,
                        # The assistant's closing text was already landed as its
                        # own message; the result must not publish it twice.
                        isinstance(event, AgentResult) and not event.is_error,
                        False,
                    )
                    delivered += 1
                if _ends_the_turn(record):
                    await self.activity(
                        self.session.project_id, self.session.topic_id, work_id, False
                    )
                reader.landed(through=entry.key)
            return delivered
