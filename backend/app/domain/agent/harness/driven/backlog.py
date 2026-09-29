"""Read a mirrored journal's unlanded tail one page at a time.

A pass reads from the landing cursor, a page per call, and never holds more than
that page: a room whose cursor fell behind can have a million records waiting,
and a reader that loaded them all did so on every pass until they landed. What a
harness adds is how it gets ready to read (the state a later page depends on,
worked out before the first one) and what one of its rows is as a
``HarnessEvent``; the paging, the cursor and retention are the same for all.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.driven.journal import Journal


class JournalBacklog[J: Journal]:
    journal: type[J]

    def __init__(self, path: Path | None):
        self.path = path
        self.after = 0
        if path is None or not path.exists():
            return
        journal = self.journal(path)
        try:
            self.after = int(journal.recall("landed") or 0)
            self.prepare(journal)
        finally:
            journal.close()

    def prepare(self, journal: J) -> None:
        """What reading from the landing cursor needs to know first."""

    def event(self, row: dict, now: datetime) -> HarnessEvent:
        raise NotImplementedError

    def unread(self) -> list[HarnessEvent]:
        if self.path is None or not self.path.exists():
            return []
        journal = self.journal(self.path)
        try:
            page = journal.read(self.after)
        finally:
            journal.close()
        if page:
            self.after = page[-1]["sequence"]
        now = datetime.now(UTC)
        return [self.event(row, now) for row in page]

    def landed(self, *, through: str) -> None:
        assert self.path is not None
        journal = self.journal(self.path)
        try:
            journal.acknowledge(int(through))
        finally:
            journal.close()

    def forget(self, *, older_than_s: float) -> None:
        if self.path is None or not self.path.exists():
            return
        journal = self.journal(self.path)
        try:
            journal.prune(
                (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
            )
        finally:
            journal.close()


def age(row: dict, now: datetime) -> float:
    """How long ago the journal recorded this row."""
    return (now - datetime.fromisoformat(row["at"])).total_seconds()
