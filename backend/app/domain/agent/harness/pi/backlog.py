"""The unread tail of one pi session, as the platform consumes it.

Two of this protocol's six calls are nearly empty here, and that is a fact about
pi rather than an omission. ``unfinished`` and ``give_up`` exist because a
harness that reports partial output can be caught mid-sentence — Claude Code
flushes a message in pieces, so the platform must not move its cursor past a
piece whose message is still arriving. pi's entry log has no such state: an
entry appears when its message is complete, and the streaming half lives in the
live event stream, which is not what this reads. So nothing is ever unfinished
and there is never a fragment to hand over.

A pass starts from what the platform has LANDED, not from what was last read.
Anything reported but not landed is reported again, and the entry id makes that
harmless — which is the whole recovery story: a reader that dies mid-pass costs
a re-read, never an event.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.harness.pi.journal import Journal
from app.domain.agent.service import AgentEvent, AgentMessage


class PiBacklog:
    def __init__(self, path: Path | None, session_id: str | None = None):
        self.path = path
        self.assembler = Assembler(session_id)
        self.entries: list[HarnessEvent] = []
        if path is None or not path.exists():
            return
        journal = Journal(path)
        try:
            landed = int(journal.recall("landed") or 0)
            # Resume the running total of a turn a previous pass landed part of.
            for entry in journal.between(journal.turn_started_at(landed), landed):
                self.assembler.absorb(entry)
            now = datetime.now(UTC)
            after = landed
            while page := journal.read(after):
                for row in page:
                    at = datetime.fromisoformat(row["at"])
                    self.entries.append(
                        HarnessEvent(
                            key=f"{row['sequence']:019d}",
                            eid=f"pi:{row['entry']['id']}",
                            record=row["entry"],
                            age_s=(now - at).total_seconds(),
                        )
                    )
                after = page[-1]["sequence"]
        finally:
            journal.close()

    def unread(self) -> list[HarnessEvent]:
        return self.entries

    def assemble(self, entry: HarnessEvent) -> list[AgentEvent]:
        if not isinstance(entry.record, dict):
            return []
        return self.assembler.accept(entry.record)

    def unfinished(self) -> set[str]:
        return set()

    def give_up(self) -> list[AgentMessage]:
        return []

    def landed(self, *, through: str) -> None:
        assert self.path is not None
        journal = Journal(self.path)
        try:
            journal.acknowledge(int(through))
        finally:
            journal.close()

    def forget(self, *, older_than_s: float) -> None:
        if self.path is None or not self.path.exists():
            return
        journal = Journal(self.path)
        try:
            journal.prune(
                (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
            )
        finally:
            journal.close()
