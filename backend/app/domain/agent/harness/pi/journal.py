"""pi's entries, mirrored where they outlive both the reader and the backend.

pi keeps its own session file, and that file is the reason a turn survives a
backend restart — but it lives on the machine pi runs on and is only readable
by asking pi. This mirror is what the platform reads from a cursor instead, and
what a pass that dies halfway through re-reads on the next one.

The dedup key is pi's own entry id rather than a sequence we invent. Importing
the same page twice is therefore harmless by construction, which is what makes
「断线之后从 ``since`` 再要一遍」 a safe recovery rather than a duplicate write.
Ordering is still ours: the sequence records arrival, because ids are opaque and
the parent chain is a linked list nobody wants to walk on every read.
"""

import json
from datetime import UTC, datetime

from app.domain.agent.harness.driven import journal

#: Records the RUNNER writes into this log beside pi's own entries, for what pi
#: says only on its live event stream. pi persists a failed model call as an
#: assistant entry and only afterwards says, on the stream, whether it will try
#: again — so the entry log alone cannot tell a request being retried from a
#: turn that failed. These two say which it was.
#: One retry of a failed request is under way (pi's ``auto_retry_start``).
RETRYING = "cheese_retrying"
#: pi is not trying again: the failed request is how the turn ends.
GAVE_UP = "cheese_gave_up"


class Journal(journal.Journal):
    table = "entries"
    column = "entry"
    schema = """
        CREATE TABLE IF NOT EXISTS entries (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            entry TEXT NOT NULL
        );
    """

    def import_entries(self, entries: list[dict]) -> None:
        """Land a page, then advance the cursor we ask pi from.

        In that order: the cursor may only claim what is already durable, or a
        crash between the two loses entries nobody will ask for again.
        """
        with self.connection:
            for entry in entries:
                self.connection.execute(
                    "INSERT OR IGNORE INTO entries(id, role, recorded_at, entry) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        entry["id"],
                        (entry.get("message") or {}).get("role", ""),
                        datetime.now(UTC).isoformat(),
                        json.dumps(entry, ensure_ascii=False),
                    ),
                )
            # The cursor pi is asked from names pi's own entries only: the
            # runner's records (``RETRYING``, ``GAVE_UP``) are ids pi never saw.
            ours = [e for e in entries if e.get("type") not in (RETRYING, GAVE_UP)]
            if ours:
                self.remember("received", ours[-1]["id"])

    def sequence_of(self, entry_id: str) -> int:
        """Where a pi entry id sits in arrival order, or 0 when we never saw it.

        A cursor the caller kept from a previous connection may name an entry
        this mirror no longer holds; answering 0 replays rather than skips,
        which the entry ids then make harmless.
        """
        row = self.connection.execute(
            "SELECT sequence FROM entries WHERE id=?", (entry_id,)
        ).fetchone()
        return row[0] if row else 0

    def turn_started_at(self, sequence: int) -> int:
        """Where the turn containing this point began — the last thing a person
        said at or before it, or 0 when the mirror does not reach that far.

        A pass resumes mid-turn whenever the previous one landed part of it, and
        the running total for that turn has to resume with it.
        """
        row = self.connection.execute(
            "SELECT MAX(sequence) FROM entries WHERE role='user' AND sequence <= ?",
            (sequence,),
        ).fetchone()
        return row[0] or 0

    def between(self, after: int, through: int) -> list[dict]:
        return [
            json.loads(entry)
            for (entry,) in self.connection.execute(
                "SELECT entry FROM entries WHERE sequence > ? AND sequence <= ? "
                "ORDER BY sequence",
                (after, through),
            )
        ]
