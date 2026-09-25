"""A harness driven through ``harness/driven`` loses nothing and repeats nothing.

The harness here is a stand-in with the smallest protocol that still has turns:
an input is recorded as what the person said, followed by one reply. Everything
between it and the room is the shared machinery — the runner's socket and input
ledger, the journal on both ends, and the drain — reached the way the backend
reaches it, over the runner's Unix socket.
"""

import asyncio
import functools
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.domain.agent.harness import HarnessEvent, SessionRef
from app.domain.agent.harness.driven import journal, runner, subscription
from app.domain.agent.harness.driven.runner import socket_path
from app.domain.agent.service import AgentMessage, AgentResult


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
                (datetime.now(UTC).isoformat(), json.dumps(record)),
            )

    def import_page(self, rows: list[dict]) -> None:
        with self.connection:
            for row in rows:
                self.connection.execute(
                    "INSERT OR IGNORE INTO records VALUES (?, ?, ?)",
                    (row["sequence"], row["at"], json.dumps(row["record"])),
                )
            if rows:
                self.advance("received", rows[-1]["sequence"])


class Runner(runner.Runner[Journal]):
    def __init__(self, state: Path):
        super().__init__(state, Journal, "records.sqlite")
        self.submitted: list[str] = []

    async def start(self) -> None:
        self.claim()
        await self.listen(2**16)

    async def _submit(self, text: str, work_id: str) -> dict:
        self.submitted.append(text)
        stamp = {"cheese": {"work_id": work_id}}
        self.journal.append({"said": text, **stamp})
        self.journal.append({"reply": f"heard {text}", **stamp})
        return {"accepted": text}

    async def dispatch(self, method: str, params: dict) -> dict:
        if method == "records":
            return {"records": self.journal.read(params["after"])}
        if method == "send":
            return await self.accept(
                params["input_id"],
                {"text": params["text"], "work_id": params["work_id"]},
                lambda: self._submit(params["text"], params["work_id"]),
            )
        raise ValueError(method)


async def call(state: Path, method: str, params: dict) -> dict:
    """What the connector relays: one JSON line each way."""
    reader, writer = await asyncio.open_unix_connection(socket_path(state))
    try:
        writer.write(json.dumps({"method": method, "params": params}).encode() + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
    finally:
        writer.close()
        await writer.wait_closed()
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


class Backlog:
    def __init__(self, path: Path):
        self.path = path
        self.entries: list[HarnessEvent] = []
        mirror = Journal(path)
        try:
            after = int(mirror.recall("landed") or 0)
            while page := mirror.read(after):
                self.entries += [
                    HarnessEvent(
                        key=f"{row['sequence']:019d}",
                        eid=f"stand-in:{row['sequence']}",
                        record=row["record"],
                        age_s=0,
                    )
                    for row in page
                ]
                after = page[-1]["sequence"]
        finally:
            mirror.close()

    def unread(self) -> list[HarnessEvent]:
        return self.entries

    def assemble(self, entry: HarnessEvent) -> list:
        assert isinstance(entry.record, dict)
        if "reply" in entry.record:
            return [
                AgentMessage(text=entry.record["reply"]),
                AgentResult(text="", session_id=None, is_error=False),
            ]
        return []

    def unfinished(self) -> set[str]:
        return set()

    def give_up(self) -> list[AgentMessage]:
        return []

    def landed(self, *, through: str) -> None:
        mirror = Journal(self.path)
        try:
            mirror.acknowledge(int(through))
        finally:
            mirror.close()

    def forget(self, *, older_than_s: float) -> None:
        mirror = Journal(self.path)
        try:
            mirror.prune(
                (datetime.now(UTC) - timedelta(seconds=older_than_s)).isoformat()
            )
        finally:
            mirror.close()


class Subscription(subscription.Subscription[Backlog]):
    async def receive(self) -> None:
        mirror = await self.on_disk(Journal, self.path)
        try:
            while True:
                after = int(await self.on_disk(mirror.recall, "received") or 0)
                page = (await self.call("records", {"after": after}))["records"]
                await self.on_disk(mirror.import_page, page)
                if len(page) < journal.PAGE:
                    return
        finally:
            await self.on_disk(mirror.close)

    def reader(self) -> Backlog:
        return Backlog(self.path)

    def starts_turn(self, record: dict, reader: Backlog) -> bool:
        return "said" in record

    def ends_turn(self, record: dict, reader: Backlog) -> bool:
        return "reply" in record

    def unowned(self, entry: HarnessEvent, reader: Backlog) -> None:
        pass


class Room:
    """The room's persistence: what it took, and a way to make it fail once."""

    def __init__(self):
        self.landed: list[tuple[str, str]] = []
        self.turns: list[bool] = []
        self.refuse_next = False

    async def consume(self, project, topic, work, event, eid, seen, unsolicited):
        if self.refuse_next:
            self.refuse_next = False
            raise ConnectionError("the database went away")
        self.landed.append((eid, type(event).__name__))

    async def activity(self, project, topic, work, active):
        self.turns.append(active)


def subscribe(state: Path, mirror: Path, room: Room) -> Subscription:
    session = SessionRef(uuid.uuid4(), uuid.uuid4(), harness="stand-in")
    return Subscription(
        session,
        mirror,
        lambda method, params: call(state, method, params),
        room.consume,
        room.activity,
    )


def send(input_id: str, text: str, work_id: str = "work") -> dict:
    return {"input_id": input_id, "text": text, "work_id": work_id}


@pytest.mark.anyio
async def test_a_restarted_runner_is_read_on_from_its_cursor_exactly_once(
    tmp_path,
):
    state, mirror, room = tmp_path / "state", tmp_path / "mirror.sqlite", Room()
    first = Runner(state)
    await first.start()
    try:
        await call(state, "send", send("one", "first", str(uuid.uuid4())))
        assert await subscribe(state, mirror, room).drain() == 2
        await call(state, "send", send("two", "second", str(uuid.uuid4())))
        # The room fails on the second turn's reply: the mirror has received
        # it, the room never took it.
        room.refuse_next = True
        with pytest.raises(ConnectionError):
            await subscribe(state, mirror, room).drain()
    finally:
        await first.close()

    # Both ends come back: a new runner on the same state directory, and a new
    # reader on the same mirror.
    second = Runner(state)
    await second.start()
    try:
        await call(state, "send", send("three", "third", str(uuid.uuid4())))
        assert await subscribe(state, mirror, room).drain() == 4
        assert await subscribe(state, mirror, room).drain() == 0
    finally:
        await second.close()

    assert [eid for eid, _ in room.landed] == [
        "stand-in:2",
        "stand-in:2",
        "stand-in:4",
        "stand-in:4",
        "stand-in:6",
        "stand-in:6",
    ]
    assert [kind for _, kind in room.landed] == ["AgentMessage", "AgentResult"] * 3
    # Every turn opened once and closed once, including the one that failed
    # halfway: its opening had landed, so only its reply was read again.
    assert room.turns == [True, False, True, False, True, False]


@pytest.mark.anyio
async def test_an_input_id_reused_for_different_text_is_refused(tmp_path):
    state = tmp_path / "state"
    first = Runner(state)
    await first.start()
    try:
        accepted = await call(state, "send", send("one", "first"))
        with pytest.raises(RuntimeError, match="cannot be reused for different text"):
            await call(state, "send", send("one", "other"))
    finally:
        await first.close()

    second = Runner(state)
    await second.start()
    try:
        with pytest.raises(RuntimeError, match="cannot be reused for different text"):
            await call(state, "send", send("one", "other"))
        # The same input asked for again is answered, not sent again.
        assert await call(state, "send", send("one", "first")) == accepted
        assert second.submitted == []
    finally:
        await second.close()

    assert accepted == {"accepted": "first"}
    rows = Journal(state / "records.sqlite")
    try:
        assert [row["record"].get("said") for row in rows.read()] == ["first", None]
    finally:
        rows.close()


#: Only guards a hang: the tests below wait on events, never on how long
#: something takes.
HANG_S = 30


class Disk:
    """The mirror's disk, where one sync can be held until the test lets it go.

    Holds the ``hold``-th commit to ``path`` and no other: enough to see what
    else runs while a mirror waits on its disk.
    """

    def __init__(self, path: Path, hold: int = 1):
        self.syncing = asyncio.Event()
        self.let_go = threading.Event()
        self.synced = threading.Event()
        self.held: list[bool] = []
        loop = asyncio.get_running_loop()
        commits = 0
        disk = self

        class Connection(sqlite3.Connection):
            def __init__(self, database, *args, **kwargs):
                super().__init__(database, *args, **kwargs)
                self.mirror = Path(database) == path

            def __exit__(self, *exc):
                nonlocal commits
                commits += self.mirror
                if not (self.mirror and commits == hold):
                    return super().__exit__(*exc)
                loop.call_soon_threadsafe(disk.syncing.set)
                disk.held.append(disk.let_go.wait(HANG_S))
                try:
                    return super().__exit__(*exc)
                finally:
                    disk.synced.set()

        self.connect = functools.partial(sqlite3.connect, factory=Connection)


async def start(state: Path, *texts: str) -> Runner:
    running = Runner(state)
    await running.start()
    for text in texts:
        await call(state, "send", send(text, text, str(uuid.uuid4())))
    return running


@pytest.mark.anyio
async def test_a_room_waiting_on_its_disk_does_not_hold_up_another_room(
    tmp_path, monkeypatch
):
    """Every room on a backend is drained on one event loop. While one room's
    mirror waits for a sync, another room's drain runs from start to finish."""
    slow, other = Room(), Room()
    runners = [
        await start(tmp_path / "slow", "a", "b", "c"),
        await start(tmp_path / "other", "x", "y"),
    ]
    try:
        disk = Disk(tmp_path / "slow.sqlite")
        # After the runners opened their own journals: only mirrors get it.
        monkeypatch.setattr(journal.sqlite3, "connect", disk.connect)

        async def other_room() -> int:
            await disk.syncing.wait()
            delivered = await subscribe(
                tmp_path / "other", tmp_path / "other.sqlite", other
            ).drain()
            disk.let_go.set()
            return delivered

        delivered = await asyncio.gather(
            subscribe(tmp_path / "slow", tmp_path / "slow.sqlite", slow).drain(),
            other_room(),
        )
        # The held sync was let go by the other room, not by the hang guard.
        assert disk.held == [True]
        assert delivered == [6, 4]
        assert (
            await subscribe(tmp_path / "slow", tmp_path / "slow.sqlite", slow).drain()
            == 0
        )
    finally:
        for running in runners:
            await running.close()

    assert [eid for eid, _ in slow.landed] == [
        f"stand-in:{sequence}" for sequence in (2, 2, 4, 4, 6, 6)
    ]
    assert [eid for eid, _ in other.landed] == [
        f"stand-in:{sequence}" for sequence in (2, 2, 4, 4)
    ]


@pytest.mark.anyio
async def test_a_reader_let_go_mid_write_is_released_after_the_write(
    tmp_path, monkeypatch
):
    """A drain cancelled while its mirror syncs leaves the write running. The
    next reader of that mirror starts after it, and lands each record once."""
    state, mirror, room = tmp_path / "state", tmp_path / "mirror.sqlite", Room()
    running = await start(state, "a", "b")
    try:
        # The first commit imports the page; the second lands the first record,
        # the write a cancelled drain leaves running with nobody waiting on it.
        disk = Disk(mirror, hold=2)
        monkeypatch.setattr(journal.sqlite3, "connect", disk.connect)
        first = subscribe(state, mirror, room)
        drain = asyncio.create_task(first.drain())
        await disk.syncing.wait()
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)

        release = asyncio.create_task(first.release())
        # Released while the write is still syncing would be too early. A
        # release that does not wait finishes well inside this; one that does
        # cannot finish at all until the write is let go.
        await asyncio.wait({release}, timeout=0.2)
        assert not release.done()
        disk.let_go.set()
        await asyncio.wait_for(release, HANG_S)
        assert disk.synced.is_set()

        assert await subscribe(state, mirror, room).drain() == 4
        assert await subscribe(state, mirror, room).drain() == 0
    finally:
        await running.close()

    assert [eid for eid, _ in room.landed] == [
        f"stand-in:{sequence}" for sequence in (2, 2, 4, 4)
    ]
