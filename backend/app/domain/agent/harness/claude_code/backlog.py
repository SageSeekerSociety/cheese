"""Mirror the runner's journal, and read its unlanded tail as room events.

Mirroring is also where the facts are worked out (``events.bind``): which card
each sub-thread is on, what each call was, and the state the room's controls
show. They are derived in journal order as each page lands, and committed with
it, because a later pass starts at the landing cursor and the record that bound
a sub-thread to its card may be far behind it.
"""

import json
from collections import ChainMap
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.claude_code.events import Assembler, bind
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.driven.backlog import JournalBacklog, age
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.service import AgentEvent

FACT = "fact:"
CONTROL = "control:"
#: How many finished tasks the controls keep listing.
TASKS_KEPT = 50


def control_facts(record: dict, known: Mapping[str, str]) -> dict[str, str]:
    """What a record changes about the state the room's controls show.

    Tasks by id (the build's own),
    and the session's latest ``init`` — its model, tools and permission mode.
    """
    if record.get("type") != "system":
        return {}
    subtype = record.get("subtype")
    if subtype == "init":
        return {
            f"{CONTROL}init": json.dumps(
                {
                    key: record.get(key)
                    for key in ("model", "permissionMode", "tools", "mcp_servers")
                },
                ensure_ascii=False,
            )
        }
    task = record.get("task_id")
    if not task or not str(subtype).startswith("task_"):
        return {}
    key = f"{CONTROL}task:{task}"
    current = json.loads(known.get(key) or "{}")
    update = {
        name: value
        for name, value in record.items()
        if name not in ("cheese", "uuid", "session_id", "type", "patch")
        and value is not None
    }
    update.update(record.get("patch") or {})
    if subtype == "task_started" and "status" not in update:
        update["status"] = "running"
    if subtype == "task_notification":
        update["status"] = record.get("status") or "completed"
    return {key: json.dumps({**current, **update, "task_id": task}, ensure_ascii=False)}


@dataclass
class Known:
    """What the mirror holds that every pass needs: where receiving got to, the
    facts, the controls' state. Read from the mirror once, then kept in step
    with it here: only a pass of ``receive`` writes any of them, on the mirror's
    own thread, and this copy changes only after the mirror took the write.

    Reading them back on every pass was a whole-table read of a table that only
    grows, twice a pass, ten passes a second for a room in a turn, whether or
    not the runner had anything new; with a few dozen rooms it held more of the
    backend's one interpreter than everything else it does.
    """

    received: int
    facts: dict[str, str]
    controls: dict[str, str]

    @classmethod
    def read(cls, path: Path) -> "Known":
        journal = Journal(path)
        try:
            return cls(
                received=int(journal.recall("received") or 0),
                facts=journal.facts(FACT),
                controls={
                    CONTROL + key: value
                    for key, value in journal.facts(CONTROL).items()
                },
            )
        finally:
            journal.close()


async def receive(
    path: Path,
    call: Callable[[str, dict], Awaitable[dict]],
    on_disk: Callable[..., Awaitable[Any]],
    known: Known,
) -> bool:
    """Mirror what the runner has that we do not; True if a task moved.

    Only tasks are worth telling an open room about: they start and finish
    while somebody watches, while the session's ``init`` is the same every turn
    and is read when the controls are opened.
    """
    moved = False
    while True:
        entries = (await call("events", {"after": known.received}))["events"]
        if not entries:
            return moved
        learned: dict[str, str] = {}
        facts: dict[str, str] = {}
        controls: dict[str, str] = {}
        for entry in entries:
            found = bind(entry["record"], ChainMap(facts, known.facts))
            facts.update(found)
            learned.update({FACT + key: value for key, value in found.items()})
            changed = control_facts(entry["record"], ChainMap(controls, known.controls))
            controls.update(changed)
            learned.update(changed)
            moved = moved or any(":task:" in key for key in changed)
        await on_disk(_import, path, entries, learned)
        known.facts.update(facts)
        known.controls.update(controls)
        known.received = entries[-1]["sequence"]
        if len(entries) < PAGE:
            return moved


def _import(path: Path, entries: list[dict], learned: dict[str, str]) -> None:
    journal = Journal(path)
    try:
        journal.import_records(entries, learned)
    finally:
        journal.close()


def control_state(path: Path | None) -> dict:
    """The mirrored state the room's controls show: tasks, newest first."""
    if path is None or not path.exists():
        return {"tasks": {}, "state": {}}
    journal = Journal(path)
    try:
        rows = journal.facts(CONTROL)
    finally:
        journal.close()
    tasks = {
        key.removeprefix("task:"): json.loads(value)
        for key, value in rows.items()
        if key.startswith("task:")
    }
    running = {k: v for k, v in tasks.items() if v.get("status") == "running"}
    finished = [k for k, v in tasks.items() if v.get("status") != "running"]
    kept = {**running, **{k: tasks[k] for k in finished[-TASKS_KEPT:]}}
    state = {"init": json.loads(rows["init"])} if "init" in rows else {}
    return {"tasks": kept, "state": state}


class ClaudeCodeBacklog(JournalBacklog[Journal]):
    journal = Journal

    def __init__(
        self,
        path: Path | None,
        session_id: str | None = None,
        facts: dict[str, str] | None = None,
    ):
        # What the pass learns while it reads stays with the pass: the mirror's
        # facts are ahead of the landing cursor, and replaying older records
        # must not write back over what they already say. A caller that keeps
        # them (`Known`) hands them over; one that does not has them read here.
        self.given = facts
        self.assembler = Assembler(ChainMap({}, facts or {}), session_id)
        super().__init__(path)

    def prepare(self, journal: Journal) -> None:
        if self.given is None:
            self.assembler.facts = ChainMap({}, journal.facts(FACT))

    def event(self, row: dict, now: datetime) -> HarnessEvent:
        record = row["record"]
        return HarnessEvent(
            key=f"{row['sequence']:019d}",
            eid=f"claude:{record.get('uuid') or row['sequence']}",
            record=record,
            age_s=age(row, now),
        )

    def assemble(self, entry: HarnessEvent) -> list[AgentEvent]:
        if not isinstance(entry.record, dict):
            return []
        return self.assembler.accept(entry.record)

    def unfinished(self) -> set[str]:
        # A record is whole when it is written: nothing arrives in pieces.
        return set()
