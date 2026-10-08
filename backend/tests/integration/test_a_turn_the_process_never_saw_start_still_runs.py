"""Which turns a room shows as running, when the process showing them has not
watched all of them.

The broker keeps the turns it is relaying in this process's memory. A deploy
replaces the process — and while it rolls out, two run side by side — so a turn
started before it, or on the other container, is unknown to the backend a page
connects to next. The page is then told nobody is working: the in-progress step
of a teammate's checklist stops turning and the room's activity line goes
empty, while the teammate is still at work. The database knows better: the
turn's interval is open.

The drift runs the other way too, and that is the half a page cannot heal from
by reconnecting: a backend that took a turn in keeps it until its own process
ends, because the ``turn_finished`` that ends it goes out on the container that
ran it and never reaches this one. The room shows 「正在思考」 for a turn that
is over. Both directions are read from the turn table while the page stays
connected (``turn_adoption.watch_books``).

Below, the broker has seen none of the room's turns. One is open and
delivered, one has stopped, one was never delivered; only the first is work in
progress.
"""

import asyncio
import queue
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.agent import turn_adoption
from app.domain.agent.models import AgentTurn
from tests.integration.conftest import chat_ws_url, post_project

SEAT = "cheese-seat-busy"

#: Shorter than any heal window a test would sit through. `watch_books` reads
#: this at the top of every tick, so patching it before the socket connects is
#: enough.
TICK_S = 0.05


def _room(client) -> str:
    project = post_project(client, {"name": "P"}, owner="alice").json()
    return project["data"]["root_topic_id"]


def _a_turn(
    client,
    room: str,
    *,
    stopped: bool = False,
    delivered: bool = True,
) -> uuid.UUID:
    """One interval in ``room``, as the turn that opened it left it behind."""
    turn_id = uuid.uuid4()
    started = datetime.now(UTC) - timedelta(minutes=7)

    async def _insert() -> None:
        async with client.test_factory() as s:
            s.add(
                AgentTurn(
                    id=turn_id,
                    conversation_id=uuid.UUID(room),
                    continuation_id=uuid.uuid4(),
                    author="alice",
                    content="做这件事",
                    is_resume=False,
                    resendable=True,
                    started_at=started,
                    delivered_at=started if delivered else None,
                    stopped_at=started if stopped else None,
                    agent_handle=SEAT,
                )
            )
            await s.commit()

    asyncio.run(_insert())
    return turn_id


def _seed(client, room: str) -> dict[str, uuid.UUID]:
    """One turn of each kind, as a room a previous process left behind."""
    return {
        "open": _a_turn(client, room),
        "stopped": _a_turn(client, room, stopped=True),
        "undelivered": _a_turn(client, room, delivered=False),
    }


def _stop(client, turn_id: uuid.UUID) -> None:
    """End an interval the way the container running the turn ends it: the row
    is stamped there, and the frame it publishes goes out there.

    Nothing of that reaches this process — which is the whole point.
    """

    async def _stamp() -> None:
        async with client.test_factory() as s:
            row = await s.get(AgentTurn, turn_id)
            assert row is not None
            row.stopped_at = datetime.now(UTC)
            await s.commit()

    asyncio.run(_stamp())


def _frames_on_connect(ws) -> dict[str, dict]:
    """What a fresh socket is told before anything happens in the room: every
    frame it gets before the answer to its first ping."""
    ws.send_json({"type": "ping"})
    seen: dict[str, dict] = {}
    while True:
        frame = ws.receive_json()
        if frame["type"] == "pong":
            return seen
        seen[frame["type"]] = frame


class _Frames:
    """One socket, read on a thread of its own.

    A frame that never arrives has to fail the test rather than block it, and
    the test client's socket has no read timeout. The thread is the only reader
    once it starts; the test body only sends.
    """

    def __init__(self, ws) -> None:
        self._queue: queue.Queue[dict] = queue.Queue()
        threading.Thread(target=self._pump, args=(ws,), daemon=True).start()

    def _pump(self, ws) -> None:
        while True:
            try:
                self._queue.put(ws.receive_json())
            except Exception:  # noqa: BLE001 — the socket is gone; so is its feed
                return

    def next(self, within_s: float = 10.0) -> dict:
        try:
            return self._queue.get(timeout=within_s)
        except queue.Empty:
            raise AssertionError(f"no frame arrived within {within_s}s") from None

    def until(self, kind: str, within_s: float = 10.0) -> dict:
        """The next frame of this type, or a failure naming the deadline."""
        deadline = time.monotonic() + within_s
        while True:
            remaining = deadline - time.monotonic()
            assert remaining > 0, f"no {kind} frame within {within_s}s"
            frame = self.next(within_s=remaining)
            if frame["type"] == kind:
                return frame

    def drain(self, within_s: float = 0.5) -> list[dict]:
        """Whatever is queued now, then whatever else lands before the window
        closes."""
        out: list[dict] = []
        deadline = time.monotonic() + within_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return out
            try:
                out.append(self._queue.get(timeout=remaining))
            except queue.Empty:
                return out


def _room_state(ws, frames: _Frames) -> dict:
    """What a room answers when a page asks 「这儿什么在跑」 — the frame a
    reconnecting client reconciles against."""
    ws.send_json({"type": "sync"})
    return frames.until("room_state")


def test_a_page_connecting_to_a_backend_that_never_saw_the_turn_start_is_told_it_runs(
    client,
):
    room = _room(client)
    turns = _seed(client, room)

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        seen = _frames_on_connect(ws)

    assert "turn_active" in seen, sorted(seen)
    active = seen["turn_active"]
    assert active["turn_ids"] == [str(turns["open"])]
    assert active["agents"] == {str(turns["open"]): SEAT}
    assert [m["member"] for m in seen["activity_snapshot"]["members"]] == [SEAT]


def test_the_turn_ending_ends_it_for_the_next_page(client, stub_hooks):
    room = _room(client)
    turns = _seed(client, room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        _frames_on_connect(ws)

    _stop(client, turns["open"])
    from app.api.deps import get_broker

    broker = get_broker()
    asyncio.run(
        broker.publish(room, {"type": "turn_finished", "turn_id": str(turns["open"])})
    )

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        seen = _frames_on_connect(ws)
    assert "turn_active" not in seen


def test_a_turn_that_ends_on_the_other_container_stops_showing_as_running(
    client, monkeypatch: pytest.MonkeyPatch
):
    """A room left busy by a turn that is over heals without the page doing
    anything.

    The turn runs on the other backend of an overlapping rollout. That backend
    ends it and publishes ``turn_finished`` on ITS channel; none of that
    reaches the backend this page is connected to. Without this the room showed
    「正在思考」 until the process was replaced.
    """
    monkeypatch.setattr(turn_adoption, "RECONCILE_EVERY_S", TICK_S)
    room = _room(client)
    turns = _seed(client, room)

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        seen = _frames_on_connect(ws)
        assert seen["turn_active"]["turn_ids"] == [str(turns["open"])]
        frames = _Frames(ws)

        _stop(client, turns["open"])

        ended = frames.until("turn_finished")
        assert ended["turn_id"] == str(turns["open"])
        assert _room_state(ws, frames)["turn_ids"] == []


def test_a_turn_running_on_the_other_container_shows_while_connected(
    client, monkeypatch: pytest.MonkeyPatch
):
    """The same drift from the side that makes a busy room look idle: a turn
    starts on the other backend after this page connected, so this process
    never sees its ``turn_started``.
    """
    monkeypatch.setattr(turn_adoption, "RECONCILE_EVERY_S", TICK_S)
    room = _room(client)

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        seen = _frames_on_connect(ws)
        assert "turn_active" not in seen, "the room starts idle"
        frames = _Frames(ws)

        started = _a_turn(client, room)

        assert frames.until("turn_started")["turn_id"] == str(started)
        state = _room_state(ws, frames)
        assert state["turn_ids"] == [str(started)]
        assert state["agents"] == {str(started): SEAT}


def test_a_turn_the_database_has_not_ended_is_left_alone(
    client, monkeypatch: pytest.MonkeyPatch
):
    """Silence is not a conclusion. A turn the database holds open, and one it
    has no row for at all, are work in progress — not work that is over.

    A turn's interval opens a moment after the turn starts, so a session's own
    turn has no row here for its first moments; a turn being fed is undelivered
    for the whole of its boot. Treating either as ended would show a room as
    idle exactly while its agent is starting up.
    """
    monkeypatch.setattr(turn_adoption, "RECONCILE_EVERY_S", TICK_S)
    from app.api.deps import get_broker

    broker = get_broker()
    room = _room(client)
    undelivered = _a_turn(client, room, delivered=False)
    unborn = uuid.uuid4()  # no row anywhere, like a turn not yet filed

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        _frames_on_connect(ws)
        frames = _Frames(ws)
        for turn_id in (undelivered, unborn):
            # Published on the portal's loop: the socket's relay is reading
            # from it.
            client.portal.call(
                broker.publish,
                room,
                {"type": "turn_started", "turn_id": str(turn_id), "agent": SEAT},
            )

        # Several ticks, so an ending the reconciler got wrong has every chance
        # to arrive.
        time.sleep(TICK_S * 8)
        arrived = frames.drain()

        assert [f["type"] for f in arrived if f["type"] == "turn_finished"] == []
        assert str(undelivered) in broker.active_turn_ids(room)
        assert str(unborn) in broker.active_turn_ids(room)
        assert _room_state(ws, frames)["turn_ids"] == sorted(
            [str(undelivered), str(unborn)]
        )
