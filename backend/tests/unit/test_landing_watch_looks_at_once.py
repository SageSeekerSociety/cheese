"""The merges being watched are looked at at the same time, and the report reads
the same as it did one at a time.

A look waits on the forge — checks, the parent commit, whether a deployment
includes the merge — and asks it with no database session open, so the waiting
is what one merge costs the tick, not what it costs the others. The rule, stated
before the change: every merge still being watched is looked at once per tick,
and one that fails is reported without holding up or hiding the rest.

What these tests pin is what a reader of the tick's result can see: several
looks are in flight together instead of one after another, and the counts and
error lines come out in the order the merges were read — so a tick that looks at
eight merges at once reports what the eight-at-a-time tick reported.
"""

import asyncio
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.domain.review import landing_watch

#: Long enough that eight of them one after another is unmistakably longer than
#: eight of them together, short enough to stay well under the layer's ceiling.
LOOK_S = 0.2


class _Session:
    """The reads `watch_landings` makes for itself before it fans out."""

    def __init__(self, ids: list[uuid.UUID]) -> None:
        self._ids = ids

    async def commit(self) -> None: ...

    async def scalars(self, _statement):
        return list(self._ids)


def _chat(ids: list[uuid.UUID]) -> SimpleNamespace:
    @asynccontextmanager
    async def sessions():
        yield _Session(ids)

    return SimpleNamespace(session_factory=sessions)


async def _nothing(*_args, **_kwargs) -> None:
    """A line said in a room; the looks under it are faked here."""


def _tick(monkeypatch, ids: list[uuid.UUID], look):
    """`watch_landings` down to the looks: nothing else on this tick has work."""

    async def gone_on_while_working(_sessions, **_kwargs):
        return []

    async def close_overdue(_session, _chat, **_kwargs):
        return []

    monkeypatch.setattr(landing_watch, "go_on_while_working", gone_on_while_working)
    monkeypatch.setattr(landing_watch, "close_overdue", close_overdue)
    monkeypatch.setattr(landing_watch, "tell_room_undelivered", _nothing)
    monkeypatch.setattr(landing_watch, "_look", look)
    return _chat(ids)


async def _watch(chat):
    return await landing_watch.watch_landings(
        chat, _nothing, record_instruction=None, task_teammate=None
    )


async def test_the_merges_are_looked_at_at_once(monkeypatch):
    # Twice the ceiling, so a run that ignores it is unmistakable: the pool the
    # looks are bounded to leave free is the same one the rest of the process
    # serves requests from.
    ids = [uuid.uuid4() for _ in range(landing_watch.AT_ONCE * 2)]
    in_flight = 0
    most_in_flight = 0

    async def look(_chat, _landing_id, **_kwargs):
        nonlocal in_flight, most_in_flight
        in_flight += 1
        most_in_flight = max(most_in_flight, in_flight)
        try:
            await asyncio.sleep(LOOK_S)
        finally:
            in_flight -= 1
        return 1

    chat = _tick(monkeypatch, ids, look)
    started = asyncio.get_running_loop().time()
    result = await _watch(chat)
    spent = asyncio.get_running_loop().time() - started

    assert most_in_flight > 1, "the merges were looked at one at a time"
    assert most_in_flight <= landing_watch.AT_ONCE, (
        f"{most_in_flight} looks at once, over the {landing_watch.AT_ONCE} this "
        "may hold: every one of them is a session off the database pool"
    )
    assert spent < LOOK_S * len(ids) / 2, (
        f"{len(ids)} looks of {LOOK_S}s took {spent:.2f}s: that is the serial "
        "time, not the time of looks that overlap"
    )
    assert result["landings_looked_at"] == len(ids)


async def test_a_merge_whose_look_fails_is_reported_and_holds_up_no_other(
    monkeypatch,
):
    ids = [uuid.uuid4() for _ in range(4)]
    boom = ids[1]
    looked_at: list[uuid.UUID] = []

    async def look(_chat, landing_id, **_kwargs):
        looked_at.append(landing_id)
        await asyncio.sleep(LOOK_S)
        if landing_id == boom:
            raise RuntimeError("the forge said no")
        return 1

    chat = _tick(monkeypatch, ids, look)
    result = await _watch(chat)

    assert sorted(looked_at) == sorted(ids), "a failed look stopped the rest"
    assert result["landings_looked_at"] == len(ids) - 1
    assert result["errors"] == [f"{boom}: the forge said no"]


async def test_the_report_reads_as_it_did_one_merge_at_a_time(monkeypatch):
    """Two failing merges, one of them the slowest: the error lines still come
    out in the order the merges were read, not in the order they finished."""
    ids = [uuid.uuid4() for _ in range(4)]

    async def look(_chat, landing_id, **_kwargs):
        await asyncio.sleep(LOOK_S * (4 - ids.index(landing_id)))
        if landing_id in ids[:2]:
            raise RuntimeError(f"no forge for {landing_id}")
        return 1

    chat = _tick(monkeypatch, ids, look)
    result = await _watch(chat)

    assert result["errors"] == [
        f"{ids[0]}: no forge for {ids[0]}",
        f"{ids[1]}: no forge for {ids[1]}",
    ]
    assert result["landings_looked_at"] == 2
