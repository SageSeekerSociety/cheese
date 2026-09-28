"""Mirror the runner's durable log before handing events to room persistence."""

from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.harness.codex.journal import Journal
from app.domain.agent.harness.driven.backlog import JournalBacklog, age
from app.domain.agent.harness.driven.journal import PAGE


async def receive(
    path: Path,
    call: Callable[[str, dict], Awaitable[dict]],
    on_disk: Callable[..., Awaitable[Any]],
) -> None:
    journal = await on_disk(Journal, path)
    try:
        after = int(await on_disk(journal.recall, "received") or 0)
        while True:
            entries = (await call("events", {"after": after}))["events"]
            await on_disk(journal.import_events, entries)
            if len(entries) < PAGE:
                return
            after = entries[-1]["sequence"]
    finally:
        await on_disk(journal.close)


class CodexBacklog(JournalBacklog[Journal]):
    journal = Journal

    def __init__(self, path: Path | None):
        self.assembler = Assembler()
        super().__init__(path)

    def prepare(self, journal: Journal) -> None:
        self.assembler.children = journal.children()

    def event(self, row: dict, now: datetime) -> HarnessEvent:
        record = row["record"]
        record.setdefault(
            "emittedAtMs", datetime.fromisoformat(row["at"]).timestamp() * 1000
        )
        params = record.get("params", {})
        item = params.get("item", {})
        item_id = params.get("itemId") or item.get("id")
        thread_id = params.get("threadId") or params.get("thread", {}).get(
            "id", "server"
        )
        eid = (
            f"codex:{thread_id}:{item_id}"
            if item_id
            else f"codex:{thread_id}:event:{row['sequence']}"
        )
        return HarnessEvent(
            key=f"{row['sequence']:019d}",
            eid=eid,
            record=record,
            age_s=age(row, now),
        )

    def assemble(self, entry: HarnessEvent):
        if not isinstance(entry.record, dict):
            return []
        return self.assembler.accept(entry.record)

    def unfinished(self) -> set[str]:
        return set(self.assembler.pending)
