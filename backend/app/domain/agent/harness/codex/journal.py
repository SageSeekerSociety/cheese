"""Codex events, recorded by the runner and mirrored by the backend.

The runner numbers events as they arrive; that number is the event's identity on
both ends, so the mirror imports it as given and a page asked for twice lands
once.
"""

import json
from datetime import UTC, datetime

from app.domain.agent.harness.driven import journal


class Journal(journal.Journal):
    table = "events"
    column = "record"
    schema = """
        CREATE TABLE IF NOT EXISTS events (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            record TEXT NOT NULL
        );
    """

    def append(self, record: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO events(recorded_at, record) VALUES (?, ?)",
                (datetime.now(UTC).isoformat(), json.dumps(record, ensure_ascii=False)),
            )

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
                self.advance("received", entries[-1]["sequence"])

    def children(self) -> dict[str, tuple[str, str]]:
        return {
            key.removeprefix("child:"): tuple(json.loads(value))
            for key, value in self.connection.execute(
                "SELECT key, value FROM state WHERE key LIKE 'child:%'"
            )
        }
