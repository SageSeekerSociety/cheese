"""A Claude Code session's journal reaches the room whatever records it holds.

The drain walks the mirrored journal in order and moves the landing cursor
only past what the room took, so a record it cannot read stops the room at
that record on every later drain too: the session goes on working and nothing
it does is shown again.
"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code.backlog import ClaudeCodeBacklog
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.harness.driven import backlog, subscription
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.service import AgentMessage, AgentResult


async def _settle_receipt(receipt):
    """These tests exercise output delivery; receipt persistence is available."""


def _journal(work: str) -> list[dict]:
    stamp = {"work_id": work}
    records = [
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "isReplay": True,
            "message": {"role": "user", "content": "clean up the build"},
            "cheese": {
                **stamp,
                "turn_start": True,
                "receipt": True,
                "receipt_work_id": work,
                "receipt_session_id": "native-session",
            },
        },
        {
            "type": "assistant",
            "uuid": "call",
            "message": {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Bash",
                        "input": {"command": "cd /tmp/x && rm -rf *"},
                    }
                ],
            },
            "cheese": stamp,
        },
        # What the build writes when it refuses a command: its `message` is
        # the reason, as a string.
        {
            "type": "system",
            "subtype": "permission_denied",
            "tool_name": "Bash",
            "tool_use_id": "toolu_1",
            "message": "This Bash command contains multiple operations.",
            "uuid": "denied",
            "cheese": stamp,
        },
        {
            "type": "user",
            "uuid": "refused",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "is_error": True,
                        "content": "Permission to use Bash was denied.",
                    }
                ],
            },
            "cheese": stamp,
        },
        {
            "type": "assistant",
            "uuid": "reply",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "I could not remove it."}],
            },
            "cheese": stamp,
        },
        {
            "type": "result",
            "uuid": "result",
            "subtype": "success",
            "is_error": False,
            "result": "I could not remove it.",
            "session_id": "s",
            "cheese": stamp,
        },
    ]
    at = datetime.now(UTC).isoformat()
    return [
        {"sequence": number, "at": at, "record": record}
        for number, record in enumerate(records, start=1)
    ]


@pytest.mark.anyio
async def test_a_refused_command_does_not_stop_the_room_reading_the_turn(tmp_path):
    work = uuid.uuid4()
    journal = _journal(str(work))
    landed: list[object] = []
    turns: list[bool] = []
    pulses: list[frozenset[str]] = []

    async def call(method: str, params: dict) -> dict:
        assert method == "events"
        return {"events": [e for e in journal if e["sequence"] > params["after"]]}

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        landed.append(event)

    async def activity(project, seat, work_id, active):
        turns.append(active)

    async def announce():
        pass

    async def moved(work_id, marks, taken):
        pulses.append(marks)

    reading = Subscription(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
        tmp_path / "records.sqlite",
        call,
        consume,
        activity,
        session_id="native-session",
        recipient_handle="cheese-a",
        announce=announce,
        receipts=_settle_receipt,
        moved=moved,
    )
    try:
        await reading.drain()
        assert turns == [True, False]
        assert [e.text for e in landed if isinstance(e, AgentMessage)] == [
            "I could not remove it."
        ]
        assert any(isinstance(e, AgentResult) for e in landed)
        assert any(subscription.returned("toolu_1") in marks for marks in pulses)

        # The cursor moved past the turn: the next drain has nothing to repeat.
        before = len(landed)
        await reading.drain()
        assert len(landed) == before
    finally:
        await reading.release()


def _long_turn(work: str, said: int) -> list[dict]:
    """One turn in which the session said ``said`` things before its result."""
    stamp = {"work_id": work}
    records = [
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "isReplay": True,
            "message": {"role": "user", "content": "go"},
            "cheese": {
                **stamp,
                "turn_start": True,
                "receipt": True,
                "receipt_work_id": work,
                "receipt_session_id": "native-session",
            },
        },
        *(
            {
                "type": "assistant",
                "uuid": f"said-{number}",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"line {number}"}],
                },
                "cheese": stamp,
            }
            for number in range(said)
        ),
        {
            "type": "result",
            "uuid": "result",
            "subtype": "success",
            "is_error": False,
            "result": f"line {said - 1}",
            "session_id": "s",
            "cheese": stamp,
        },
    ]
    at = datetime.now(UTC).isoformat()
    return [
        {"sequence": number, "at": at, "record": record}
        for number, record in enumerate(records, start=1)
    ]


class _Paging(Subscription):
    """The subscription as the backend builds it, noting each page it is handed."""

    pages: list[int]

    def reader(self) -> ClaudeCodeBacklog:
        reader = super().reader()
        unread = reader.unread

        def noted() -> list:
            page = unread()
            self.pages.append(len(page))
            return page

        reader.unread = noted  # type: ignore[method-assign]
        return reader


@pytest.mark.anyio
async def test_a_backlog_many_pages_long_reaches_the_room_a_page_at_a_time(tmp_path):
    said = 5 * PAGE + 17
    journal = _long_turn(str(uuid.uuid4()), said)
    landed: list[str] = []
    fail_at: set[str] = {"line 700"}

    async def call(method: str, params: dict) -> dict:
        return {
            "events": [e for e in journal if e["sequence"] > params["after"]][:PAGE]
        }

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        if isinstance(event, AgentMessage):
            if event.text in fail_at:
                fail_at.discard(event.text)
                raise ConnectionError("the database went away")
            landed.append(event.text)

    async def activity(project, seat, work_id, active):
        pass

    async def announce():
        pass

    def reading() -> _Paging:
        made = _Paging(
            SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
            tmp_path / "records.sqlite",
            call,
            consume,
            activity,
            session_id="native-session",
            recipient_handle="cheese-a",
            announce=announce,
            receipts=_settle_receipt,
        )
        made.pages = []
        return made

    first = reading()
    try:
        with pytest.raises(ConnectionError):
            await first.drain()
    finally:
        await first.release()
    assert landed == [f"line {n}" for n in range(700)]

    # The room failed partway through a page. What it took before that stays
    # taken: the next drain starts at the record that failed, not at the top.
    second = reading()
    try:
        await second.drain()
        await second.drain()
    finally:
        await second.release()
    assert landed == [f"line {n}" for n in range(said)]
    assert second.pages and max(second.pages) <= PAGE


def _said_turn(work: str, text: str, *, first: int, at: datetime) -> list[dict]:
    """A turn the session started by itself, saying ``text``, recorded at ``at``."""
    stamp = {"work_id": work, "unsolicited": True}
    records = [
        {
            "type": "assistant",
            "uuid": f"{work}-said",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": text}],
            },
            "cheese": {**stamp, "turn_start": True},
        },
        {
            "type": "result",
            "uuid": f"{work}-result",
            "subtype": "success",
            "is_error": False,
            "result": text,
            "session_id": "s",
            "cheese": stamp,
        },
    ]
    return [
        {"sequence": number, "at": at.isoformat(), "record": record}
        for number, record in enumerate(records, start=first)
    ]


@pytest.mark.anyio
async def test_what_nobody_read_for_hours_never_reaches_the_room(tmp_path):
    now = datetime.now(UTC)
    old, fresh, later = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    # Two days of a session's output that no drain took, then what it says now.
    journal = [
        *_said_turn(old, "answered two days late", first=1, at=now - timedelta(days=2)),
        *_said_turn(fresh, "said just now", first=3, at=now),
    ]
    landed: list[str] = []
    opened: list[str] = []

    async def call(method: str, params: dict) -> dict:
        return {"events": [e for e in journal if e["sequence"] > params["after"]]}

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        if isinstance(event, AgentMessage):
            landed.append(event.text)

    async def activity(project, seat, work_id, active):
        if active:
            opened.append(str(work_id))

    async def announce():
        pass

    def reading() -> Subscription:
        return Subscription(
            SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
            tmp_path / "records.sqlite",
            call,
            consume,
            activity,
            session_id="native-session",
            recipient_handle="cheese-a",
            announce=announce,
            receipts=_settle_receipt,
        )

    first = reading()
    try:
        await first.drain()
    finally:
        await first.release()
    assert landed == ["said just now"]
    assert opened == [fresh]

    # A reader that comes back later is not handed the old output either, and
    # what the session says next still arrives.
    journal.extend(_said_turn(later, "said next", first=5, at=datetime.now(UTC)))
    second = reading()
    try:
        await second.drain()
    finally:
        await second.release()
    assert landed == ["said just now", "said next"]
    assert opened == [fresh, later]


@pytest.mark.anyio
async def test_a_turn_whose_inputs_cannot_be_proven_still_ends_in_the_room(tmp_path):
    """A session on a runner older than the input protocol: its turns settle
    from what the journal retained. One that read an input without the exact
    receipt identity can never be settled that way, and the room still has to
    hear the turn end and everything the session says after it."""
    now = datetime.now(UTC)
    work, later = str(uuid.uuid4()), str(uuid.uuid4())
    stamp = {"work_id": work, "agent_handle": "cheese-a"}
    turn = [
        {
            "type": "user",
            "uuid": str(uuid.uuid4()),
            "isReplay": True,
            # The echo itself does not say which session read it.
            "message": {"role": "user", "content": "check the build"},
            "cheese": {
                **stamp,
                "turn_start": True,
                "receipt": True,
                "receipt_work_id": work,
                "receipt_session_id": "native-session",
            },
        },
        {
            "type": "assistant",
            "uuid": "reply",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "The build is green."}],
            },
            "cheese": stamp,
        },
        {
            "type": "result",
            "uuid": "result",
            "subtype": "success",
            "is_error": False,
            "result": "The build is green.",
            "session_id": "native-session",
            "cheese": stamp,
        },
    ]
    journal = [
        {"sequence": number, "at": now.isoformat(), "record": record}
        for number, record in enumerate(turn, start=1)
    ]
    journal.extend(_said_turn(later, "said next", first=4, at=now))
    ended: list[str] = []
    said: list[str] = []
    settled: list[object] = []

    async def call(method: str, params: dict) -> dict:
        return {"events": [e for e in journal if e["sequence"] > params["after"]]}

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        if isinstance(event, AgentResult):
            ended.append(str(work_id))
        elif isinstance(event, AgentMessage):
            said.append(event.text)

    async def activity(project, seat, work_id, active):
        pass

    async def announce():
        pass

    async def completion(value):
        settled.append(value)

    reading = Subscription(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
        tmp_path / "records.sqlite",
        call,
        consume,
        activity,
        session_id="native-session",
        recipient_handle="cheese-a",
        announce=announce,
        receipts=_settle_receipt,
        completions=completion,
        input_protocol=None,
    )
    try:
        await reading.drain()
        assert ended == [work, later]
        assert said == ["The build is green.", "said next"]
        assert settled == []

        # Landed once: the next drain has nothing to repeat.
        await reading.drain()
        assert ended == [work, later]
    finally:
        await reading.release()


class _Clock:
    """The backend's clock, moved on by hand."""

    def __init__(self, monkeypatch):
        self.ahead = timedelta()
        real = datetime

        class Shifted(datetime):
            @classmethod
            def now(cls, tz=None):
                return real.now(tz) + self.ahead

        monkeypatch.setattr(backlog, "datetime", Shifted)


@pytest.mark.anyio
@pytest.mark.parametrize("refusals", [2, None])
async def test_a_record_the_room_goes_on_refusing_is_stepped_over(
    tmp_path, monkeypatch, refusals
):
    """``refusals`` None: the room never takes "line 2". 2: it refuses it twice,
    as while its database was away, and takes it after that."""
    clock = _Clock(monkeypatch)
    journal = _long_turn(str(uuid.uuid4()), 5)
    landed: list[str] = []
    refused: list[str] = []

    async def call(method: str, params: dict) -> dict:
        return {"events": [e for e in journal if e["sequence"] > params["after"]]}

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        if isinstance(event, AgentMessage):
            if event.text == "line 2" and (refusals is None or len(refused) < refusals):
                refused.append(event.text)
                raise ValueError("the room cannot take this")
            landed.append(event.text)

    async def activity(project, seat, work_id, active):
        pass

    async def announce():
        pass

    session = SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code")

    async def drain() -> None:
        reading = Subscription(
            session,
            tmp_path / "records.sqlite",
            call,
            consume,
            activity,
            session_id="native-session",
            recipient_handle="cheese-a",
            announce=announce,
            receipts=_settle_receipt,
        )
        try:
            await reading.drain()
        finally:
            await reading.release()

    # Refused on drain after drain, minutes apart: nothing past it arrives yet.
    for _ in range(2):
        with pytest.raises(ValueError):
            await drain()
        clock.ahead += timedelta(minutes=1)
    assert landed == ["line 0", "line 1"]

    clock.ahead += timedelta(minutes=10)
    if refusals is None:
        # Refused a third time, ten minutes after the first: the rest of the
        # turn reaches the room, and the refused record never does.
        await drain()
        assert landed == ["line 0", "line 1", "line 3", "line 4"]
    else:
        # A record the room refused only for a while still arrives, in order.
        await drain()
        assert landed == ["line 0", "line 1", "line 2", "line 3", "line 4"]

    await drain()
    assert len(landed) == (4 if refusals is None else 5)


@pytest.mark.anyio
async def test_a_day_of_unread_output_is_stepped_over_without_reading_it(tmp_path):
    now = datetime.now(UTC)
    old = [
        {**row, "at": (now - timedelta(days=1)).isoformat()}
        for row in _long_turn(str(uuid.uuid4()), 5 * PAGE)
    ]
    # Only expired output: neither a receipt nor an attributed legacy result.
    # Both are retained independently until their input can be reconciled.
    old[0]["record"]["cheese"].pop("receipt")
    old[-1]["record"]["cheese"] = {}
    journal = [
        *old,
        *_said_turn(str(uuid.uuid4()), "said just now", first=len(old) + 1, at=now),
        # Recorded by a clock that runs behind, after something said just now.
        *_said_turn(
            str(uuid.uuid4()),
            "recorded by a slow clock",
            first=len(old) + 3,
            at=now - timedelta(days=1),
        ),
        *_said_turn(str(uuid.uuid4()), "said next", first=len(old) + 5, at=now),
    ]
    landed: list[str] = []

    async def call(method: str, params: dict) -> dict:
        return {
            "events": [e for e in journal if e["sequence"] > params["after"]][:PAGE]
        }

    async def consume(project, topic, work_id, event, eid, seen, unsolicited):
        if isinstance(event, AgentMessage):
            landed.append(event.text)

    async def activity(project, seat, work_id, active):
        pass

    async def announce():
        pass

    reading = _Paging(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
        tmp_path / "records.sqlite",
        call,
        consume,
        activity,
        session_id="native-session",
        recipient_handle="cheese-a",
        announce=announce,
        receipts=_settle_receipt,
    )
    reading.pages = []
    try:
        await reading.drain()
    finally:
        await reading.release()

    assert landed == ["said just now", "said next"]
    # The day-old run was never read: one page holds what is left, and the
    # empty read that ends the walk.
    assert reading.pages == [6, 0]
