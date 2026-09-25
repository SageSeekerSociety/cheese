"""Deliver journaled Claude Code records through the room's persistence callbacks.

A turn begins at the record the runner marked as its start — the echo of the
input that opened it, or the first thing a session said when nothing of ours
woke it — and ends at ``result``. An input counts as read when its echo comes
back (``--replay-user-messages``), which for words said mid-turn is the next
tool boundary: that echo, not the write, is the receipt.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path

from app.domain.agent.harness import (
    ActivityConsumer,
    EventConsumer,
    HarnessEvent,
    ReceiptConsumer,
    SessionRef,
)
from app.domain.agent.harness.claude_code.backlog import ClaudeCodeBacklog, receive
from app.domain.agent.harness.driven import subscription
from app.domain.agent.service import AgentEvent


def said(record: dict) -> str:
    """The text of a user message as it was written to stdin."""
    content = (record.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                return str(part.get("text") or "")
    return ""


class Subscription(subscription.Subscription[ClaudeCodeBacklog]):
    def __init__(
        self,
        session: SessionRef,
        path: Path,
        call: Callable[[str, dict], Awaitable[dict]],
        consume: EventConsumer,
        activity: ActivityConsumer,
        *,
        session_id: str | None,
        announce: Callable[[], Awaitable[None]],
        receipts: ReceiptConsumer | None = None,
        pulse: subscription.Pulse | None = None,
    ):
        super().__init__(
            session, path, call, consume, activity, receipts=receipts, pulse=pulse
        )
        self.session_id = session_id
        self.announce = announce

    async def receive(self) -> None:
        if await receive(self.path, self.call):
            await self.announce()

    def reader(self) -> ClaudeCodeBacklog:
        return ClaudeCodeBacklog(self.path, self.session_id)

    def starts_turn(self, record: dict, reader: ClaudeCodeBacklog) -> bool:
        return bool((record.get("cheese") or {}).get("turn_start"))

    def ends_turn(self, record: dict, reader: ClaudeCodeBacklog) -> bool:
        return record.get("type") == "result"

    def unowned(self, entry: HarnessEvent, reader: ClaudeCodeBacklog) -> None:
        # Before the first input, and the turns the platform runs for itself
        # (`/reload-plugins`): the session starting up says nothing the room
        # has to hear. Their facts were already taken when they were mirrored.
        reader.assemble(entry)

    def receipt(self, record: dict) -> str | None:
        if (record.get("cheese") or {}).get("receipt"):
            return said(record)
        return None

    def marks(self, record: dict, events: list[AgentEvent]) -> set[str]:
        marks = subscription.marks_of(events)
        message = record.get("entry") if record.get("type") == "cheese_file" else record
        content = ((message or {}).get("message") or {}).get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                marks |= {
                    subscription.PROGRESS,
                    subscription.returned(str(block.get("tool_use_id"))),
                }
        return marks
