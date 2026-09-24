"""Claude Code's stdout, recorded by the runner and mirrored by the backend.

The runner numbers each line as it arrives, and that number is the record's
identity on both ends, so the mirror imports it as given and a page asked for
twice lands once. Claude Code's own ``uuid`` rides inside the record and is what
the translated events are keyed by.

Travels in the runner archive, so it imports nothing outside the standard
library and the shared journal.
"""

import json
from datetime import UTC, datetime

from app.domain.agent.harness.driven import journal


class Journal(journal.Journal):
    table = "records"
    column = "record"
    schema = """
        CREATE TABLE IF NOT EXISTS records (
            sequence INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            record TEXT NOT NULL
        );
    """

    def append(self, record: dict) -> None:
        with self.connection:
            self.connection.execute(
                "INSERT INTO records(recorded_at, record) VALUES (?, ?)",
                (datetime.now(UTC).isoformat(), json.dumps(record, ensure_ascii=False)),
            )

    def import_records(self, entries: list[dict], facts: dict[str, str]) -> None:
        """Commit a remote page, the facts derived from it, then the cursor.

        ``facts`` are state rows worked out from the page by the reader (which
        card a sub-thread is on, what a task is doing): written in the same
        commit as the records they come from, so a reader never sees one
        without the other.
        """
        with self.connection:
            for entry in entries:
                self.connection.execute(
                    "INSERT OR IGNORE INTO records(sequence, recorded_at, record) "
                    "VALUES (?, ?, ?)",
                    (
                        entry["sequence"],
                        entry["at"],
                        json.dumps(entry["record"], ensure_ascii=False),
                    ),
                )
            for key, value in facts.items():
                self.connection.execute(
                    "INSERT INTO state VALUES (?, ?) ON CONFLICT(key) "
                    "DO UPDATE SET value=excluded.value",
                    (key, value),
                )
            if entries:
                self.advance("received", entries[-1]["sequence"])

    def expire(self, before: str) -> None:
        """Drop records older than ``before``, landed or not.

        The runner's side of retention: it cannot know what a reader took, and
        a reader that has not asked for a week has no room left to land in.
        """
        with self.connection:
            self.connection.execute(
                f"DELETE FROM {self.table} WHERE recorded_at < ?", (before,)
            )

    def facts(self, prefix: str) -> dict[str, str]:
        return {
            key.removeprefix(prefix): value
            for key, value in self.connection.execute(
                "SELECT key, value FROM state WHERE key LIKE ?", (prefix + "%",)
            )
        }
