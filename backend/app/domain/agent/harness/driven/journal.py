"""A harness's records, kept where they outlive both the reader and the backend.

The same journal serves both ends of the wire: the runner records into one on
the session machine, and the backend mirrors it into another from a cursor.
Each has three tables — the harness's records in arrival order, the inputs
already accepted, and a key-value state that holds the cursors — and only the
records table differs by harness, because only the harness knows what makes one
of its records the same record twice.

Two cursors live in the state table. ``received`` is how far the mirror has
asked the other end; ``landed`` is how far the room has persisted. The first
only ever claims what is already durable here, and the second only what the
room has taken, so a reader that dies anywhere re-reads rather than skips.
"""

import json
import sqlite3
from pathlib import Path

PAGE = 256


class Journal:
    #: The records table's name and the column holding each record. They differ
    #: by harness only because journals already on session machines were
    #: created under these names, and a runner started by a new deployment
    #: reopens the journal the old one wrote.
    table: str
    column: str
    #: ``CREATE TABLE`` for the records table; it must have ``sequence``,
    #: ``recorded_at`` and ``column``.
    schema: str

    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript(
            self.schema
            + """
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
            """
        )

    def read(self, after: int = 0) -> list[dict]:
        return [
            {"sequence": seq, "at": at, "record": json.loads(record)}
            for seq, at, record in self.connection.execute(
                f"SELECT sequence, recorded_at, {self.column} FROM {self.table} "
                "WHERE sequence > ? ORDER BY sequence LIMIT ?",
                (after, PAGE),
            )
        ]

    def advance(self, key: str, through: int) -> None:
        """Move a sequence cursor forward, never back.

        Not in a transaction of its own, so an import can advance its cursor
        in the same commit as the page it landed.
        """
        self.connection.execute(
            "INSERT INTO state VALUES (?, ?) ON CONFLICT(key) "
            "DO UPDATE SET value=CAST(MAX(CAST(value AS INTEGER), "
            "CAST(excluded.value AS INTEGER)) AS TEXT)",
            (key, str(through)),
        )

    def acknowledge(self, through: int) -> None:
        with self.connection:
            self.advance("landed", through)

    def prune(self, before: str) -> None:
        with self.connection:
            self.connection.execute(
                f"DELETE FROM {self.table} WHERE sequence <= ? AND recorded_at < ?",
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

    # --- inputs: at most once, however many times we are asked ---------------

    def input(self, identifier: str) -> tuple[str, str, dict | None] | None:
        # By position: Codex journals already on session machines name the
        # payload column ``text``.
        row = self.connection.execute(
            "SELECT * FROM inputs WHERE id=?", (identifier,)
        ).fetchone()
        return (row[1], row[2], json.loads(row[3]) if row[3] else None) if row else None

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

    def close(self) -> None:
        self.connection.close()
