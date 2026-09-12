"""Mirror the runner's durable log before handing events to room persistence."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.codex.events import Assembler
from app.domain.agent.harness.codex.journal import Journal


async def receive(path: Path, call: Callable[[str, dict], Awaitable[dict]]) -> None:
    journal = Journal(path)
    try:
        after = int(journal.recall("received") or 0)
        while True:
            entries = (await call("events", {"after": after}))["events"]
            journal.import_events(entries)
            if len(entries) < 256:
                return
            after = entries[-1]["sequence"]
    finally:
        journal.close()


class CodexBacklog:
    def __init__(self, path: Path):
        self.path = path
        self.assembler = Assembler()
        self.entries: list[HarnessEvent] = []
        if not path.exists():
            return
        journal = Journal(path)
        try:
            self.assembler.children = journal.children()
            after = int(journal.recall("landed") or 0)
            now = datetime.now(UTC)
            while entries := journal.read(after):
                for entry in entries:
                    record = entry["record"]
                    record.setdefault(
                        "emittedAtMs",
                        datetime.fromisoformat(entry["at"]).timestamp() * 1000,
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
                        else f"codex:{thread_id}:event:{entry['sequence']}"
                    )
                    self.entries.append(
                        HarnessEvent(
                            key=f"{entry['sequence']:019d}",
                            eid=eid,
                            record=record,
                            age_s=(
                                now - datetime.fromisoformat(entry["at"])
                            ).total_seconds(),
                        )
                    )
                after = entries[-1]["sequence"]
        finally:
            journal.close()

    def unread(self) -> list[HarnessEvent]:
        return self.entries

    def assemble(self, entry: HarnessEvent):
        if not isinstance(entry.record, dict):
            return []
        return self.assembler.accept(entry.record)

    def unfinished(self) -> set[str]:
        return set(self.assembler.pending)

    def give_up(self):
        return self.assembler.give_up()

    def landed(self, *, through: str) -> None:
        journal = Journal(self.path)
        try:
            journal.acknowledge(int(through))
        finally:
            journal.close()

    def forget(self, *, older_than_s: float) -> None:
        if not self.path.exists():
            return
        journal = Journal(self.path)
        try:
            journal.prune(
                (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
            )
        finally:
            journal.close()
