"""Session-host records survive backend disconnects and reader restarts."""

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class Journal:
    def __init__(self, path: Path):
        self.connection = sqlite3.connect(path)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                record TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS inputs (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT
            );
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)

    def append(self, record: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO events(recorded_at, record) VALUES (?, ?)",
                (datetime.now(UTC).isoformat(), json.dumps(record, ensure_ascii=False)),
            )

    def read(self, after: int = 0) -> list[dict]:
        return [
            {"sequence": seq, "at": at, "record": json.loads(record)}
            for seq, at, record in self.connection.execute(
                "SELECT sequence, recorded_at, record FROM events "
                "WHERE sequence > ? ORDER BY sequence LIMIT 256",
                (after,),
            )
        ]

    def import_events(self, entries: list[dict]) -> None:
        """Commit a remote page before advancing the local receive cursor."""
        with self.connection:
            for entry in entries:
                self.connection.execute(
                    "INSERT OR IGNORE INTO events(sequence, recorded_at, record) "
                    "VALUES (?, ?, ?)",
                    (
                        entry["sequence"],
                        entry["at"],
                        json.dumps(entry["record"], ensure_ascii=False),
                    ),
                )
                record = entry["record"]
                if record["method"] == "thread/started":
                    thread = record["params"]["thread"]
                    if parent := thread.get("parentThreadId"):
                        self.connection.execute(
                            "INSERT INTO state VALUES (?, ?) ON CONFLICT(key) "
                            "DO UPDATE SET value=excluded.value",
                            (
                                f"child:{thread['id']}",
                                json.dumps([parent, thread.get("agentRole") or ""]),
                            ),
                        )
            if entries:
                self.connection.execute(
                    "INSERT INTO state VALUES ('received', ?) ON CONFLICT(key) "
                    "DO UPDATE SET value=CAST(MAX(CAST(value AS INTEGER), "
                    "CAST(excluded.value AS INTEGER)) AS TEXT)",
                    (str(entries[-1]["sequence"]),),
                )

    def children(self) -> dict[str, tuple[str, str]]:
        return {
            key.removeprefix("child:"): tuple(json.loads(value))
            for key, value in self.connection.execute(
                "SELECT key, value FROM state WHERE key LIKE 'child:%'"
            )
        }

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
                "DELETE FROM events WHERE sequence <= ? AND recorded_at < ?",
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

    def input(self, identifier: str) -> tuple[str, str, dict | None] | None:
        row = self.connection.execute(
            "SELECT text, status, result FROM inputs WHERE id=?",
            (identifier,),
        ).fetchone()
        return (row[0], row[1], json.loads(row[2]) if row[2] else None) if row else None

    def begin_input(self, identifier: str, text: str) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO inputs VALUES (?, ?, 'sending', NULL)", (identifier, text)
            )

    def finish_input(self, identifier: str, status: str, result: dict) -> None:
        with self.connection:
            self.connection.execute(
                "UPDATE inputs SET status=?, result=? WHERE id=?",
                (status, json.dumps(result), identifier),
            )

    def close(self) -> None:
        self.connection.close()
