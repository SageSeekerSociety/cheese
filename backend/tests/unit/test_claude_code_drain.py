"""A Claude Code session's journal reaches the room whatever records it holds.

The drain walks the mirrored journal in order and moves the landing cursor
only past what the room took, so a record it cannot read stops the room at
that record on every later drain too: the session goes on working and nothing
it does is shown again.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.claude_code.backlog import ClaudeCodeBacklog
from app.domain.agent.harness.claude_code.subscription import Subscription
from app.domain.agent.harness.driven import subscription
from app.domain.agent.harness.driven.journal import PAGE
from app.domain.agent.service import AgentMessage, AgentResult


def _journal(work: str) -> list[dict]:
    stamp = {"work_id": work}
    records = [
        {
            "type": "user",
            "uuid": "echo",
            "isReplay": True,
            "message": {"role": "user", "content": "clean up the build"},
            "cheese": {**stamp, "turn_start": True, "receipt": True},
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

    reading = Subscription(
        SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness="claude-code"),
        tmp_path / "records.sqlite",
        call,
        consume,
        activity,
        session_id=None,
        announce=announce,
        pulse=lambda seat, marks: pulses.append(marks),
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
            "uuid": "echo",
            "isReplay": True,
            "message": {"role": "user", "content": "go"},
            "cheese": {**stamp, "turn_start": True, "receipt": True},
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
            session_id=None,
            announce=announce,
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
