"""A session's mirror on the backend forgets what nobody will read again, and
gives the disk back.

The mirror is a sqlite file per seat that lives as long as the room does. What
it keeps past a day is only what is still going on.
"""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness.codex.backlog import CodexBacklog
from app.domain.agent.harness.codex.journal import Journal as CodexJournal

DAY_S = 24 * 3600


def _on_disk(path: Path) -> int:
    wal = path.with_name(path.name + "-wal")
    return path.stat().st_size + (wal.stat().st_size if wal.exists() else 0)


def test_a_mirror_that_forgot_a_busy_day_gives_its_disk_back(tmp_path):
    path = tmp_path / "events.sqlite"
    journal = CodexJournal(path)
    old = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    now = datetime.now(UTC).isoformat()
    delta = {"method": "item/agentMessage/delta", "params": {"delta": "x" * 1000}}
    journal.import_events(
        [{"sequence": n, "at": old, "record": delta} for n in range(1, 3001)]
    )
    journal.acknowledge(3000)
    journal.import_events(
        [{"sequence": n, "at": now, "record": delta} for n in range(3001, 3011)]
    )
    journal.close()
    busy = _on_disk(path)

    CodexBacklog(path).forget(older_than_s=DAY_S)

    connection = sqlite3.connect(path)
    try:
        kept = [row[0] for row in connection.execute("SELECT sequence FROM events")]
        (free,) = connection.execute("PRAGMA freelist_count").fetchone()
    finally:
        connection.close()
    assert kept == list(range(3001, 3011))
    assert free == 0
    assert _on_disk(path) < busy / 10
