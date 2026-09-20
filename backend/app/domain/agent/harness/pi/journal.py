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
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

PAGE = 256


class Journal:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS entries (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                role TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                entry TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inputs (
                id TEXT PRIMARY KEY,
                payload TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

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
            if entries:
                self.remember("received", entries[-1]["id"])

    def read(self, after: int = 0) -> list[dict]:
        return [
            {"sequence": seq, "at": at, "entry": json.loads(entry)}
            for seq, at, entry in self.connection.execute(
                "SELECT sequence, recorded_at, entry FROM entries "
                "WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (after, PAGE),
            )
        ]

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

    # --- inputs: at most once, however many times we are asked ---------------

    def input(self, identifier: str) -> tuple[str, str, dict | None] | None:
        row = self.connection.execute(
            "SELECT payload, status, result FROM inputs WHERE id=?", (identifier,)
        ).fetchone()
        return (row[0], row[1], json.loads(row[2]) if row[2] else None) if row else None

    def begin_input(self, identifier: str, payload: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO inputs VALUES (?, ?, 'sending', NULL)",
                (identifier, payload),
            )

    def finish_input(self, identifier: str, status: str, result: dict) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inputs SET status=?, result=? WHERE id=?",
                (status, json.dumps(result), identifier),
            )

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

    def acknowledge(self, through: int) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO state VALUES ('landed', ?) ON CONFLICT(key) "
                "DO UPDATE SET value=CAST(MAX(CAST(value AS INTEGER), "
                "CAST(excluded.value AS INTEGER)) AS TEXT)",
                (str(through),),
            )

    def prune(self, before: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM entries WHERE sequence <= ? AND recorded_at < ?",
                (int(self.recall("landed") or 0), before),
            )

    def remember(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO state VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def recall(self, key: str) -> str | None:
        row = self.connection.execute(
            "SELECT value FROM state WHERE key=?", (key,)
        ).fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self.connection.close()
