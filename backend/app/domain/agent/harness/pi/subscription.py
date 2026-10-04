"""Hand mirrored pi entries to the room's persistence, and say when it is working.

Activity comes out of the entry log itself rather than a separate signal: a turn
begins at the thing a person said and ends at the assistant message that stopped
for a reason other than a tool call or a failed model call — or, for a failed
call, at the runner's record that pi gave up on it (``journal.GAVE_UP``).
Nothing else has to be trusted to report it, which matters because the report
would have to survive the same restart the log already survives.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.domain.agent.harness import (
    EventConsumer,
    HarnessEvent,
    SessionRef,
)
from app.domain.agent.harness.driven import subscription
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.pi.backlog import PiBacklog
from app.domain.agent.harness.pi.events import CONTINUES, FAILED, thread_of
from app.domain.agent.harness.pi.journal import GAVE_UP, Journal


async def receive(
    path: Path,
    call: Callable[[str, dict], Awaitable[dict]],
    on_disk: Callable[..., Awaitable[Any]],
) -> None:
    """Pull whatever the runner has that we do not, a page at a time."""
    journal = await on_disk(Journal, path)
    try:
        since = await on_disk(journal.recall, "received")
        while True:
            page = (await call("entries", {"since": since}))["entries"]
            # The runner holds its own records too, under ids it knows, so the
            # cursor moves past them: a read that waits for news would find
            # them new for ever otherwise.
            await on_disk(
                journal.import_entries,
                page,
                cursor=("received", page[-1]["id"]) if page else None,
            )
            if len(page) < PAGE:
                return
            since = page[-1]["id"]
    finally:
        await on_disk(journal.close)


class Subscription(subscription.Subscription[PiBacklog]):
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: subscription.SeatActivity,
        session_id: str | None = None,
        *,
        moved: subscription.Moved | None = None,
    ):
        super().__init__(session, path, call, consume, activity, moved=moved)
        self.session_id = session_id

    async def receive(self) -> None:
        await receive(self.path, self.read, self.on_disk)

    def reader(self) -> PiBacklog:
        return PiBacklog(self.path, self.session_id, harness=self.session.harness)

    def starts_turn(self, record: dict, reader: PiBacklog) -> bool:
        # A subagent's prompt, or its closing message, is its own run and not
        # the session's turn (`subagents.py`).
        if thread_of(record):
            return False
        return (record.get("message") or {}).get("role") == "user"

    def ends_turn(self, record: dict, reader: PiBacklog) -> bool:
        if thread_of(record):
            return False
        if record.get("type") == GAVE_UP:
            return True
        message = record.get("message") or {}
        return message.get("role") == "assistant" and message.get("stopReason") not in (
            CONTINUES,
            FAILED,
        )

    def unowned(self, entry: HarnessEvent, reader: PiBacklog) -> None:
        # Everything before the first input belongs to no turn: the model and
        # thinking-level entries pi writes at startup.
        pass
