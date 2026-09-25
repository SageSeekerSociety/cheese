"""Hand mirrored pi entries to the room's persistence, and say when it is working.

Activity comes out of the entry log itself rather than a separate signal: a turn
begins at the thing a person said and ends at the assistant message that stopped
for a reason other than a tool call. Nothing else has to be trusted to report
it, which matters because the report would have to survive the same restart the
log already survives.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.domain.agent.harness import (
    ActivityConsumer,
    EventConsumer,
    HarnessEvent,
    SessionRef,
)
from app.domain.agent.harness.driven import subscription
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.harness.pi.backlog import PiBacklog
from app.domain.agent.harness.pi.events import CONTINUES
from app.domain.agent.harness.pi.journal import Journal


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
            await on_disk(journal.import_entries, page)
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
        activity: ActivityConsumer,
        session_id: str | None = None,
        *,
        pulse: subscription.Pulse | None = None,
    ):
        super().__init__(session, path, call, consume, activity, pulse=pulse)
        self.session_id = session_id

    async def receive(self) -> None:
        await receive(self.path, self.call, self.on_disk)

    def reader(self) -> PiBacklog:
        return PiBacklog(self.path, self.session_id)

    def starts_turn(self, record: dict, reader: PiBacklog) -> bool:
        return (record.get("message") or {}).get("role") == "user"

    def ends_turn(self, record: dict, reader: PiBacklog) -> bool:
        message = record.get("message") or {}
        return (
            message.get("role") == "assistant"
            and message.get("stopReason") != CONTINUES
        )

    def unowned(self, entry: HarnessEvent, reader: PiBacklog) -> None:
        # Everything before the first input belongs to no turn: the model and
        # thinking-level entries pi writes at startup.
        pass
