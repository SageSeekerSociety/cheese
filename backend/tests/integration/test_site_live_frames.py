"""What a room's socket hears while a turn runs, for 现场 to show it live.

现场 appends what the socket brings instead of waiting to be reopened, so every
change to its timeline has to cross the socket: a step that failed, a request
the session is retrying, a machine the turn is waiting for. Each test scripts
the stream-json records one situation produces and follows them through the
real runner, mirror, translation and chat service to the frames a browser
receives and the blocks a reload reads back.
"""

import asyncio
import uuid

from sqlalchemy import select

from app.domain.agent.device_hub import DeviceOffline
from app.domain.block.models import Block
from tests.conftest import wait_work_idle
from tests.integration.conftest import chat_ws_url, post_project, session_auth_headers


def _alice() -> dict:
    return session_auth_headers("alice")


def _room(client) -> str:
    project = post_project(client, {"name": "P", "owner_handle": "alice"}).json()
    return project["data"]["root_topic_id"]


def _until(ws, predicate) -> list[dict]:
    frames = []
    while True:
        frames.append(ws.receive_json())
        if predicate(frames[-1]):
            return frames


def _done(frame: dict) -> bool:
    return frame["type"] in ("done", "error")


def _blocks(client, room: str) -> list[Block]:
    async def read() -> list[Block]:
        async with client.test_factory() as session:
            query = (
                select(Block)
                .where(Block.topic_id == uuid.UUID(room))
                .order_by(Block.created_at)
            )
            return list(await session.scalars(query))

    return asyncio.run(read())


def _of_type(blocks: list[Block], event_type: str) -> list[Block]:
    return [b for b in blocks if (b.meta or {}).get("event_type") == event_type]


def _retry(stub, topic, attempt: int) -> None:
    stub.record(
        topic,
        type="system",
        subtype="api_retry",
        attempt=attempt,
        max_retries=10,
        retry_delay_ms=500,
        error_status=429,
        error="rate_limit",
    )


def test_a_step_that_failed_reaches_the_socket_as_that_step_restated(
    client, stub_hooks
):
    """The red mark is a change to a line already on the timeline, so it goes out
    as that line again — same id, now failed — not as a line of its own."""

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="pandoc a.md")
        stub_hooks.returns(topic, "Bash", "bash: pandoc: command not found", error=True)
        stub_hooks.stops(topic, "没有 pandoc")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 转一下"})
        frames = _until(ws, _done)

    started = next(
        f
        for f in frames
        if f["type"] == "event_block" and (f["block"]["meta"] or {}).get("tool")
    )
    updated = [f for f in frames if f["type"] == "block_updated"]
    # Restated, never added: every update is that one step (its failure, and
    # then what it printed).
    assert {f["block"]["id"] for f in updated} == {started["block"]["id"]}
    assert updated[-1]["block"]["meta"]["failed"] is True
    assert updated[-1]["block"]["meta"]["error"] == "bash: pandoc: command not found"


def test_a_streak_of_retries_is_one_line_that_counts_them(client, stub_hooks):
    """Three retries of the same request read as one line saying 3/10, not as
    three lines. Once the request goes through, the next retry is new news and
    gets a line of its own."""

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        for attempt in (1, 2, 3):
            _retry(stub_hooks, topic, attempt)
        stub_hooks.says(topic, "查到了")
        _retry(stub_hooks, topic, 1)
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 查一下"})
        frames = _until(ws, _done)
    wait_work_idle()

    retries = _of_type(_blocks(client, room), "api_retry")
    assert [b.meta["attempt"] for b in retries] == [3, 1]
    assert "3/10" in retries[0].content
    assert retries[0].meta["max_attempts"] == 10
    assert "429" in retries[0].meta["detail"]
    # Both lines belong to the turn and to the agent doing it, which is what
    # 现场 groups and filters on.
    assert all(b.turn_id is not None for b in retries)
    assert all(b.author != "system" for b in retries)

    first = str(retries[0].id)
    landed = [
        f["block"]["id"]
        for f in frames
        if f["type"] == "event_block"
        and (f["block"]["meta"] or {}).get("event_type") == "api_retry"
    ]
    restated = [
        f["block"]["meta"]["attempt"]
        for f in frames
        if f["type"] == "block_updated" and f["block"]["id"] == first
    ]
    assert landed == [first, str(retries[1].id)]
    assert restated == [2, 3]


def test_a_turn_waiting_for_its_machine_says_so_and_says_when_it_is_back(
    client, stub_hooks, monkeypatch
):
    """While the device is out of reach nothing the session does can arrive, so
    the room is told it is waiting — once — and the same line says when the
    machine is back."""
    offline = {"now": False}
    reach = stub_hooks.call

    async def call(handle, method, params):
        if offline["now"]:
            raise DeviceOffline("the device has no live link")
        return await reach(handle, method, params)

    monkeypatch.setattr(stub_hooks, "call", call)

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make build")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 编一下"})
        _until(
            ws,
            lambda f: f["type"] == "event_block" and "make build" in str(f["block"]),
        )
        offline["now"] = True
        waiting = _until(
            ws,
            lambda f: (
                f["type"] == "event_block"
                and (f["block"]["meta"] or {}).get("event_type") == "device_waiting"
            ),
        )[-1]["block"]
        offline["now"] = False
        back = _until(
            ws,
            lambda f: (
                f["type"] == "block_updated" and f["block"]["id"] == waiting["id"]
            ),
        )[-1]["block"]
        stub_hooks.returns(topic, "Bash", "built")
        stub_hooks.stops(topic, "编好了")
        _until(ws, _done)
    wait_work_idle()

    assert waiting["meta"]["state"] == "waiting"
    assert "offline" in waiting["meta"]["detail"]
    assert back["meta"]["state"] == "over"
    assert len(_of_type(_blocks(client, room), "device_waiting")) == 1


def test_a_socket_that_joins_mid_turn_learns_when_the_turn_started(client, stub_hooks):
    """A browser opened halfway through a turn has to be able to say how long it
    has been going, not how long it has been watching."""

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="sleep 60")

    stub_hooks.emit_turn = turn
    room = _room(client)
    topic = uuid.UUID(room)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 等一下"})
        started = _until(ws, lambda f: f["type"] == "turn_started")[-1]
        with client.websocket_connect(chat_ws_url(room, "alice")) as late:
            active = _until(late, lambda f: f["type"] == "turn_active")[-1]
        stub_hooks.stops(topic, "好了")
        _until(ws, _done)

    assert active["turn_ids"] == [started["turn_id"]]
    assert set(active["since"]) == {started["turn_id"]}
    assert isinstance(active["since"][started["turn_id"]], float)


def test_what_a_step_printed_is_kept_on_it_capped_and_redacted(client, stub_hooks):
    """Opening a step shows what the command printed: its end, at most 8 KiB,
    with credentials masked. The page and the socket say only that there is
    output; the text is fetched for the one step being opened."""
    printed = (
        "\n".join(f"line {n}" for n in range(3000)) + "\ntoken ghp_abcdefghijklmnop"
    )

    def turn(topic, prompt, reply):
        stub_hooks.starts(topic)
        stub_hooks.acknowledges(topic, prompt)
        stub_hooks.uses(topic, "Bash", command="make test")
        stub_hooks.returns(topic, "Bash", printed)
        stub_hooks.stops(topic, "好了")

    stub_hooks.emit_turn = turn
    room = _room(client)
    with client.websocket_connect(chat_ws_url(room, "alice")) as ws:
        ws.send_json({"type": "message", "content": "@芝士 跑测试"})
        frames = _until(ws, _done)
    wait_work_idle()

    updated = [f["block"] for f in frames if f["type"] == "block_updated"]
    assert len(updated) == 1
    step_id = updated[0]["id"]
    assert "output" not in updated[0]["meta"]
    assert updated[0]["meta"]["output_bytes"] > 8 * 1024

    page = client.get(
        f"/topics/{room}/transcript", params={"limit": 50}, headers=_alice()
    ).json()["data"]["data"]
    listed = next(b for b in page if b["id"] == step_id)
    assert "output" not in listed["meta"]

    kept = client.get(
        f"/topics/{room}/transcript/{step_id}/output", headers=_alice()
    ).json()["data"]
    assert len(kept["output"].encode()) <= 8 * 1024
    assert kept["output"].endswith("token ***")
    assert "line 2999" in kept["output"]
    assert "line 0\n" not in kept["output"]
    assert kept["bytes"] == updated[0]["meta"]["output_bytes"]


def test_a_steps_output_is_read_only_through_its_own_room(client, stub_hooks):
    """The output door is the transcript's door: someone else's room, or an id
    that is not a step of this room, answers 404."""
    room = _room(client)
    other = _room(client)
    stranger = str(uuid.uuid4())
    response = client.get(
        f"/topics/{other}/transcript/{stranger}/output", headers=_alice()
    )
    assert response.status_code == 404
    assert (
        client.get(
            f"/topics/{room}/transcript/{stranger}/output", headers=_alice()
        ).status_code
        == 404
    )
