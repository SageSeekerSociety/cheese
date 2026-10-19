"""Claude Code's stdout, recorded by the runner and read by the backend.

The runner numbers each line as it arrives, and that number is the record's
identity everywhere it goes (the room, the raw record), so a page read twice
lands once. Claude Code's own ``uuid`` rides inside the record and is what the
translated events are keyed by.

Travels in the runner archive, so it imports nothing outside the standard
library and the shared journal.
"""

import json
from datetime import UTC, datetime

from app.domain.agent.harness.driven import journal


class Journal(journal.Journal):
    table = "records"
    column = "record"
    turn_end = (
        "json_extract(record, '$.type') = 'result' "
        "AND json_extract(record, '$.cheese.work_id') IS NOT NULL"
    )
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
        self.grew()

    def expire(self, before: str) -> None:
        """Forget records older than ``before``. The journal is what the room
        shows and the platform archives; nothing the platform decides by is in
        it (``lifecycle``'s outbox is its own table)."""
        with self.connection:
            self.connection.execute(
                f"DELETE FROM {self.table} WHERE recorded_at < ?", (before,)
            )
