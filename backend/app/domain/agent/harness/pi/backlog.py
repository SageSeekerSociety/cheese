"""The unread tail of one pi session, as the platform consumes it.

One of this protocol's calls is empty here, and that is a fact about pi rather
than an omission. ``unfinished`` exists because a harness that reports partial
output can be caught mid-message, and the platform must not move its cursor
past a piece whose message is still arriving. pi's entry log has no such state:
an entry appears when its message is complete, and the streaming half lives in
the live event stream, which is not what this reads. So nothing is ever
unfinished.

A pass starts from what the platform has LANDED, not from what was last read.
Anything reported but not landed is reported again, and the entry id makes that
harmless — which is the whole recovery story: a reader that dies mid-pass costs
a re-read, never an event.
"""

from datetime import datetime
from pathlib import Path

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.driven.backlog import JournalBacklog, age
from app.domain.agent.harness.pi.events import Assembler
from app.domain.agent.harness.pi.journal import Journal
from app.domain.agent.service import AgentEvent


class PiBacklog(JournalBacklog[Journal]):
    journal = Journal

    def __init__(
        self,
        path: Path | None,
        session_id: str | None = None,
        *,
        harness: str,
    ):
        self.assembler = Assembler(session_id, harness=harness)
        super().__init__(path)

    def prepare(self, journal: Journal) -> None:
        self.assembler.generation = journal.generation()
        # Resume the running total of a turn a previous pass landed part of.
        for entry in journal.between(journal.turn_started_at(self.after), self.after):
            self.assembler.absorb(entry)

    def event(self, row: dict, now: datetime) -> HarnessEvent:
        return HarnessEvent(
            key=f"{row['sequence']:019d}",
            eid=f"pi:{row['record']['id']}",
            record=row["record"],
            age_s=age(row, now),
        )

    def assemble(self, entry: HarnessEvent) -> list[AgentEvent]:
        if not isinstance(entry.record, dict):
            return []
        entry_id = str(entry.record.get("id") or "")
        self.assembler._positions[entry_id] = int(entry.key)
        return self.assembler.accept(entry.record)

    def unfinished(self) -> set[str]:
        return set()
