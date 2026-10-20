"""A record the room goes on refusing does not hold the session's reading up.

What a Claude Code session wrote is shown in the room record by record, in
order. A record the room refuses is read again on the next pass, so one it
refused only for a while (its database away for a moment) still arrives, in
order. One it keeps refusing, ``REFUSED_TIMES`` times over ``REFUSED_OVER_S``,
is stepped over, and the rest of what the session wrote reaches the room.
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.domain.agent.harness import SessionRef
from app.domain.agent.harness.driven import reporting
from app.domain.agent.service import AgentMessage

HARNESS = "claude-code"
WORK = uuid.uuid4()


def _rows(count: int) -> list[dict]:
    now = datetime.now(UTC).isoformat()
    return [
        {
            "sequence": n,
            "at": now,
            "record": {"cheese": {"work_id": str(WORK)}, "text": f"line {n}"},
        }
        for n in range(1, count + 1)
    ]


class _Clock:
    """The reading's monotonic clock, moved by the test."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


def _reading(rows: list[dict], show) -> reporting.Reading:
    async def call(method: str, params: dict) -> dict:
        after = params.get("records_after") or 0
        return {
            "worker_epoch": 1,
            "events": [],
            "records": [row for row in rows if row["sequence"] > after],
            "state": {},
        }

    async def nothing(*args, **kwargs) -> None:
        return None

    return reporting.Reading(
        reporting.Whose(
            SessionRef(uuid.uuid4(), uuid.uuid4(), "cheese-a", harness=HARNESS),
            "cheese-a",
            uuid.uuid4(),
            "cheese-a",
            HARNESS,
            "Claude Code",
        ),
        call,
        epoch=lambda: 1,
        assemble=lambda record: [AgentMessage(text=record["text"])],
        show=show,
        hand=nothing,
        records=None,
        controls=nothing,
    )


@pytest.mark.anyio
@pytest.mark.parametrize("refusals", [2, None])
async def test_a_record_the_room_goes_on_refusing_is_stepped_over(
    monkeypatch, refusals
):
    """``refusals`` None: the room never takes "line 2". 2: it refuses it twice,
    as while its database was away, and takes it after that."""
    clock = _Clock()
    monkeypatch.setattr(reporting, "time", clock)
    landed: list[str] = []
    refused: list[str] = []

    async def show(work, event, eid, unsolicited):
        if event.text == "line 2" and (refusals is None or len(refused) < refusals):
            refused.append(event.text)
            raise ValueError("the room cannot take this")
        landed.append(event.text)

    reading = _reading(_rows(4), show)

    # Refused pass after pass, minutes apart: nothing past it arrives yet.
    for _ in range(2):
        await reading.drain()
        clock.now += 60
    assert landed == ["line 1"]

    clock.now += reporting.REFUSED_OVER_S
    await reading.drain()
    if refusals is None:
        # Refused a third time, long after the first: the rest reaches the
        # room, and the refused record never does.
        assert landed == ["line 1", "line 3", "line 4"]
    else:
        # A record the room refused only for a while still arrives, in order.
        assert landed == ["line 1", "line 2", "line 3", "line 4"]

    await reading.drain()
    assert len(landed) == (3 if refusals is None else 4)


@pytest.mark.anyio
async def test_a_record_refused_three_times_at_once_is_not_stepped_over_yet(
    monkeypatch,
):
    """Refused often but only for a moment: that is a room that was away for a
    moment, and the record waits for it."""
    clock = _Clock()
    monkeypatch.setattr(reporting, "time", clock)
    landed: list[str] = []

    async def show(work, event, eid, unsolicited):
        if event.text == "line 2":
            raise ValueError("the room cannot take this")
        landed.append(event.text)

    reading = _reading(_rows(3), show)
    for _ in range(reporting.REFUSED_TIMES + 1):
        await reading.drain()

    assert landed == ["line 1"]
