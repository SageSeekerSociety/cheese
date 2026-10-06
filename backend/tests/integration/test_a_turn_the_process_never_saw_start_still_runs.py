"""A turn still running survives a backend that never saw it start.

The broker keeps the turns it is relaying in this process's memory. A deploy
replaces the process — and while it rolls out, two run side by side — so a turn
started before it, or on the other container, is unknown to the backend a page
connects to next. The page is then told nobody is working: the in-progress step
of a teammate's checklist stops turning and the room's activity line goes
empty, while the teammate is still at work. The database knows better: the
turn's interval is open.

Below, the broker has seen none of the room's turns. One is open and
delivered, one has stopped, one was never delivered; only the first is work in
progress.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.agent.models import AgentTurn
from tests.integration.conftest import chat_ws_url, post_project

SEAT = "cheese-seat-busy"


def _room(client) -> str:
    project = post_project(client, {"name": "P"}, owner="alice").json()
    return project["data"]["root_topic_id"]


def _seed(client, room: str) -> dict[str, uuid.UUID]:
    turns = {"open": uuid.uuid4(), "stopped": uuid.uuid4(), "undelivered": uuid.uuid4()}
    started = datetime.now(UTC) - timedelta(minutes=7)

    async def _insert() -> None:
        async with client.test_factory() as s:
            for name, turn_id in turns.items():
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
                        delivered_at=None if name == "undelivered" else started,
                        stopped_at=started if name == "stopped" else None,
                        agent_handle=SEAT,
                    )
                )
            await s.commit()

    asyncio.run(_insert())
    return turns


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

    async def _stop() -> None:
        async with client.test_factory() as s:
            row = await s.get(AgentTurn, turns["open"])
            assert row is not None
            row.stopped_at = datetime.now(UTC)
            await s.commit()

    asyncio.run(_stop())
    from app.api.deps import get_broker

    broker = get_broker()
    asyncio.run(
        broker.publish(room, {"type": "turn_finished", "turn_id": str(turns["open"])})
    )

    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        seen = _frames_on_connect(ws)
    assert "turn_active" not in seen
