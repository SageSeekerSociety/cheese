"""Read a mirrored journal's unlanded tail one page at a time.

A pass reads from the landing cursor, a page per call, and never holds more than
that page: a room whose cursor fell behind can have a million records waiting,
and a reader that loaded them all did so on every pass until they landed. What a
harness adds is how it gets ready to read (the state a later page depends on,
worked out before the first one) and what one of its rows is as a
``HarnessEvent``; the paging, the cursor and retention are the same for all.
"""

import contextlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.driven.journal import Journal

#: The share of a mirror's pages left free after retention above which it is
#: rewritten (``compact``). Rewriting costs a read and a write of the whole
#: file, on the thread every read of that seat waits on.
VACUUM_FREE_SHARE = 0.25


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

    def step_over_older(self, *, than_s: float) -> int:
        """Land, unread, the run of records at the cursor older than ``than_s``,
        in one move rather than a page at a time; how many there were."""
        if self.path is None or not self.path.exists():
            return 0
        journal = self.journal(self.path)
        try:
            count, last = journal.older_run(
                self.after, (datetime.now(UTC) - timedelta(seconds=than_s)).isoformat()
            )
            if count:
                journal.acknowledge(last)
                self.after = last
        finally:
            journal.close()
        return count

    def refused(self, key: str, *, times: int, over_s: float) -> bool:
        """Note that the room refused the record ``key`` once more; True once it
        has refused it ``times`` times, over at least ``over_s`` seconds.

        Kept in the journal, not in the reader: every drain starts at the
        record that failed, and the backend that tried it last may be gone.
        """
        assert self.path is not None
        now = datetime.now(UTC)
        journal = self.journal(self.path)
        try:
            noted = json.loads(journal.recall("refused") or "{}")
            if noted.get("key") != key:
                noted = {"key": key, "times": 0, "since": now.isoformat()}
            noted["times"] += 1
            given_up = noted["times"] >= times and (
                now - datetime.fromisoformat(noted["since"])
            ) >= timedelta(seconds=over_s)
            journal.remember("refused", json.dumps(noted))
        finally:
            journal.close()
        return given_up

    def forget(self, *, older_than_s: float) -> None:
        """Drop what the mirror no longer needs, and give the space back."""
        if self.path is None or not self.path.exists():
            return
        before = (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
        journal = self.journal(self.path)
        try:
            journal.prune(before)
            self.forget_state(journal, before=before)
            compact(journal.connection)
        finally:
            journal.close()

    def forget_state(self, journal: J, *, before: str) -> None:
        """What a harness keeps beside its records and no longer needs: kept
        since before ``before`` and not about anything still going on."""


def compact(connection: sqlite3.Connection) -> None:
    """Rewrite the mirror once most of what retention freed is still on disk:
    sqlite reuses a deleted page but never gives it back on its own, so a
    mirror stays the size of the busiest day it ever had."""
    (free,) = connection.execute("PRAGMA freelist_count").fetchone()
    (pages,) = connection.execute("PRAGMA page_count").fetchone()
    if not pages or free / pages <= VACUUM_FREE_SHARE:
        return
    # A route reading the controls may hold the file this moment; the next
    # round of retention tries again.
    with contextlib.suppress(sqlite3.OperationalError):
        connection.execute("VACUUM")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def age(row: dict, now: datetime) -> float:
    """How long ago the journal recorded this row."""
    return (now - datetime.fromisoformat(row["at"])).total_seconds()
