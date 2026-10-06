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
#: pi started or finished compacting the session's context (``compaction_start``
#: / ``compaction_end``). pi writes a compaction entry only once it is over, and
#: the minutes before that are the ones a room has to hear about.
COMPACTING = "cheese_compacting"
#: A subagent the session started (``subagents.py``) began its work, or ended
#: it — finished, failed, or stopped by its parent. pi has no subagents of its
#: own, so nothing in pi's log can say either.
#: Every record the runner writes; none of them is an id pi knows.
RUNNER_RECORDS = (RETRYING, GAVE_UP, COMPACTING)
#: The key on every record of a subagent's thread: which subagent
#: (``subagents.py`` stamps it). A record without it is the session's own.
THREAD = "subagent"


class Journal(journal.Journal):
    table = "entries"
    column = "entry"
    # Every assistant message, not only the final one: which stop reasons end
    # a turn is the subscription's to say, and over-including only stops the
    # step early.
    turn_end = (
        f"json_extract(entry, '$.type') = '{GAVE_UP}' "
        "OR json_extract(entry, '$.message.role') = 'assistant'"
    )
    schema = """
        CREATE TABLE IF NOT EXISTS entries (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT NOT NULL UNIQUE,
            role TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            entry TEXT NOT NULL
        );
    """

    def import_entries(
        self,
        entries: list[dict],
        *,
        cursor: tuple[str, str] | None = None,
        owner: str | None = None,
    ) -> None:
        """Land a page, the owner the page walked to, and the cursor we ask
        pi from — in one transaction (FB-56 P2-4).

        In that order, and together: the cursor may only claim what is
        already durable, and the owner may only move with the page that
        proves it. A crash between them used to replay a page with the new
        owner already written, re-stamping the predecessor's tail as the
        successor's. The cursor writes below go through the same execute as
        the page and the owner: `remember` carries its own commit, and
        nesting it here would land the cursor ahead of the page.

        ``cursor`` names a cursor other than the session's own, and where it
        now stands: a subagent is a pi of its own, asked from its own place in
        its own log (``subagents.py``).
        """
        with self.connection:
            for entry in entries:
                self.connection.execute(
                    "INSERT OR IGNORE INTO entries(id, role, recorded_at, entry) "
                    "VALUES (?, ?, ?, ?)",
                    (
                        entry["id"],
                        # A subagent's prompt is not a person starting a turn
                        # of the session (`turn_started_at`).
                        ""
                        if entry.get(THREAD)
                        else (entry.get("message") or {}).get("role", ""),
                        datetime.now(UTC).isoformat(),
                        json.dumps(entry, ensure_ascii=False),
                    ),
                )

            def put(key: str, value: str) -> None:
                self.connection.execute(
                    "INSERT INTO state VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, value),
                )

            if owner is not None:
                put("owner", owner)
            if cursor is not None:
                put(*cursor)
            else:
                # Only native entries can be used as pi's received cursor.
                ours = [e for e in entries if e.get("type") not in RUNNER_RECORDS]
                if ours:
                    put("received", ours[-1]["id"])
        if entries:
            self.grew()

    def generation(self) -> str:
        """The mirror's own id, stable across resumes, new on a rebuild (FB-56).

        A resume reads the same file and keeps it; a rebuild creates the file
        and mints a new one. Positions are only comparable inside one
        generation — that is the epoch the platform's owner records compare.
        """
        import uuid as _uuid

        existing = self.recall("generation")
        if existing:
            return existing
        generation = _uuid.uuid4().hex
        self.remember("generation", generation)
        return generation

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
