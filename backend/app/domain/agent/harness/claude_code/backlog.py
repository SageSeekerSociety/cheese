"""Mirror the runner's journal, and read its unlanded tail as room events.

Mirroring is also where the facts are worked out (``events.bind``): which card
each sub-thread is on, what each call was, and the state the room's controls
show. They are derived in journal order as each page lands, and committed with
it, because a later pass starts at the landing cursor and the record that bound
a sub-thread to its card may be far behind it.
"""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.agent.harness import HarnessEvent
from app.domain.agent.harness.claude_code.events import Assembler, bind
from app.domain.agent.harness.claude_code.journal import Journal
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.service import AgentEvent

FACT = "fact:"
CONTROL = "control:"
#: How many finished tasks the controls keep listing.
TASKS_KEPT = 50


def control_facts(record: dict, known: dict[str, str]) -> dict[str, str]:
    """What a record changes about the state the room's controls show.

    Tasks by id (the build's own and the executor commands the runner watches),
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
    if not task or not str(subtype).startswith(("task_", "executor_task")):
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
    if subtype in ("task_started", "executor_task") and "status" not in update:
        update["status"] = "running"
    if subtype == "task_notification":
        update["status"] = record.get("status") or "completed"
    if subtype == "executor_task":
        update["task_type"] = "executor_bash"
    return {key: json.dumps({**current, **update, "task_id": task}, ensure_ascii=False)}


async def receive(path: Path, call: Callable[[str, dict], Awaitable[dict]]) -> bool:
    """Mirror what the runner has that we do not; True if a task moved.

    Only tasks are worth telling an open room about: they start and finish
    while somebody watches, while the session's ``init`` is the same every turn
    and is read when the controls are opened.
    """
    journal = Journal(path)
    moved = False
    try:
        facts = journal.facts(FACT)
        controls = {
            CONTROL + key: value for key, value in journal.facts(CONTROL).items()
        }
        after = int(journal.recall("received") or 0)
        while True:
            entries = (await call("events", {"after": after}))["events"]
            learned: dict[str, str] = {}
            for entry in entries:
                found = bind(entry["record"], facts)
                facts.update(found)
                learned.update({FACT + key: value for key, value in found.items()})
                changed = control_facts(entry["record"], controls)
                controls.update(changed)
                learned.update(changed)
                moved = moved or any(":task:" in key for key in changed)
            journal.import_records(entries, learned)
            if len(entries) < PAGE:
                return moved
            after = entries[-1]["sequence"]
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


class ClaudeCodeBacklog:
    def __init__(self, path: Path | None, session_id: str | None = None):
        self.path = path
        self.entries: list[HarnessEvent] = []
        facts: dict[str, str] = {}
        if path is not None and path.exists():
            journal = Journal(path)
            try:
                facts = journal.facts(FACT)
                after = int(journal.recall("landed") or 0)
                now = datetime.now(UTC)
                while page := journal.read(after):
                    for entry in page:
                        record = entry["record"]
                        at = datetime.fromisoformat(entry["at"])
                        self.entries.append(
                            HarnessEvent(
                                key=f"{entry['sequence']:019d}",
                                eid=f"claude:{record.get('uuid') or entry['sequence']}",
                                record=record,
                                age_s=(now - at).total_seconds(),
                            )
                        )
                    after = page[-1]["sequence"]
            finally:
                journal.close()
        self.assembler = Assembler(facts, session_id)

    def unread(self) -> list[HarnessEvent]:
        return self.entries

    def assemble(self, entry: HarnessEvent) -> list[AgentEvent]:
        if not isinstance(entry.record, dict):
            return []
        return self.assembler.accept(entry.record)

    def unfinished(self) -> set[str]:
        # A record is whole when it is written: nothing arrives in pieces.
        return set()

    def landed(self, *, through: str) -> None:
        assert self.path is not None
        journal = Journal(self.path)
        try:
            journal.acknowledge(int(through))
        finally:
            journal.close()

    def forget(self, *, older_than_s: float) -> None:
        if self.path is None or not self.path.exists():
            return
        journal = Journal(self.path)
        try:
            journal.prune(
                (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
            )
        finally:
            journal.close()
