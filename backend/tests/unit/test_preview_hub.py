"""运行环境预览's platform end: which machine a teammate's preview reaches, and
what happens when that answer changes under it.

I/O-free, like the device hub it is modelled on — the transport is a Protocol, so
a fake with a list in it is a complete machine.
"""

import asyncio
import uuid

from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub, PreviewMachine

SEAT = "cheese-one"


class Machine:
    """Answers every request with one canned response; records what it was asked."""

    def __init__(self, *, status: int = 200):
        self.status = status
        self.asked: list[str] = []
        self.attached: PreviewMachine | None = None

    def attach(
        self, hub: PreviewHub, topic_id: uuid.UUID, seat: str = SEAT, issued: int = 0
    ) -> "Machine":
        self.attached = hub.attach(topic_id, seat, self, issued=issued)
        return self

    async def send_bytes(self, data: bytes) -> None:
        op, stream, payload = wire.decode(data)
        if op != wire.OP_REQ:
            return
        meta, _ = wire.decode_meta(payload)
        self.asked.append(meta["path"])
        assert self.attached is not None
        self.attached.on_frame(
            wire.encode(
                wire.OP_RESP,
                stream,
                wire.encode_meta({"status": self.status, "headers": []}, b"body"),
            ),
        )
        self.attached.on_frame(wire.encode(wire.OP_END, stream))


class SilentMachine:
    """Connected, and never answers — an app that died behind a live tunnel."""

    def __init__(self) -> None:
        self.asked = 0

    async def send_bytes(self, data: bytes) -> None:
        if wire.decode(data)[0] == wire.OP_REQ:
            self.asked += 1


async def test_a_request_reaches_the_seats_machine_and_comes_back():
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    Machine().attach(hub, topic_id)

    response = await hub.request(
        topic_id, SEAT, method="GET", path="/index.html", headers=[]
    )

    assert response is not None
    assert response.status == 200 and response.body == b"body"


async def test_a_seat_with_no_machine_is_a_miss_not_a_hang():
    hub = PreviewHub()

    assert (
        await hub.request(uuid.uuid4(), SEAT, method="GET", path="/", headers=[])
        is None
    )


async def test_two_teammates_in_one_room_keep_their_own_tunnels():
    """Each teammate in a room serves from its own checkout, through its own
    helper. One tunnel per room made the two helpers take it from each other on
    every reconnect, and a request landing in the gap found nothing."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    one = Machine().attach(hub, topic_id, "cheese-one")
    two = Machine().attach(hub, topic_id, "cheese-two")

    await hub.request(topic_id, "cheese-one", method="GET", path="/a", headers=[])
    await hub.request(topic_id, "cheese-two", method="GET", path="/b", headers=[])

    assert one.asked == ["/a"] and two.asked == ["/b"]


async def test_the_newest_credential_owns_a_seat():
    """A seat moves — a relaunched screen, a Cloud box replaced. Keeping the old
    connection would leave the room looking at a machine the work left."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    first = Machine().attach(hub, topic_id, issued=100)
    second = Machine().attach(hub, topic_id, issued=200)

    await hub.request(topic_id, SEAT, method="GET", path="/", headers=[])

    assert second.asked == ["/"] and first.asked == []


async def test_a_helper_redialling_with_the_same_credential_takes_its_seat_back():
    """A dropped connection is redialled with the credential it already had, and
    the backend may not have noticed the old socket is dead yet."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    Machine().attach(hub, topic_id, issued=100)
    again = Machine().attach(hub, topic_id, issued=100)

    assert again.attached is not None
    await hub.request(topic_id, SEAT, method="GET", path="/", headers=[])
    assert again.asked == ["/"]


async def test_a_stale_credential_cannot_displace_the_live_one():
    """A helper left behind on a machine the seat moved off still holds a valid,
    older credential. Letting it in would hand the room the old checkout's app
    and send the current helper away."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    current = Machine().attach(hub, topic_id, issued=200)
    stale = Machine().attach(hub, topic_id, issued=100)

    await hub.request(topic_id, SEAT, method="GET", path="/", headers=[])

    assert stale.attached is None
    assert current.asked == ["/"] and stale.asked == []


async def test_the_loser_of_a_replacement_cannot_evict_the_winner():
    """Both connections tear down eventually, and the old one's teardown lands
    after the new one attached. Detaching by seat alone would take the live
    machine with it and the preview would die a moment after being restored."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    first = Machine().attach(hub, topic_id)
    Machine().attach(hub, topic_id)

    assert first.attached is not None
    hub.detach(first.attached)

    assert hub.is_online(topic_id, SEAT)


async def test_a_replaced_machines_late_frames_do_not_reach_its_replacement():
    """The machine being replaced is still delivering frames for a moment, and
    its stream ids are numbered the same way as the new machine's."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    old = hub.attach(topic_id, SEAT, SilentMachine())
    new = hub.attach(topic_id, SEAT, SilentMachine())
    assert old is not None and new is not None
    stream = hub.open_stream(topic_id, SEAT)
    assert stream is not None

    old.on_frame(wire.encode(wire.OP_ERR, stream.id, b"from the old machine"))

    assert stream.inbox.empty()


async def test_a_machine_going_away_ends_what_was_waiting_on_it():
    """A proxied WebSocket waits with no deadline — an HMR socket is silent until
    somebody edits a file. So the machine's disappearance has to arrive as a
    frame, or every viewer's browser socket is held open forever with nothing
    ever coming to end it."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    machine = hub.attach(topic_id, SEAT, SilentMachine())
    assert machine is not None
    stream = hub.open_stream(topic_id, SEAT)
    assert stream is not None

    hub.detach(machine)

    op, _payload = await stream.receive(timeout=1.0)
    assert op == wire.OP_CLOSE


async def test_probe_is_false_when_the_app_never_answers():
    """A connected helper is not a running app. Reporting `online` off the
    connection alone is what embeds an iframe that can only render a white box."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    machine = SilentMachine()
    hub.attach(topic_id, SEAT, machine)

    assert await hub.probe(topic_id, SEAT, timeout=0.05) is False
    assert machine.asked == 1, "it has to actually knock, not read a flag"


async def test_probe_is_false_when_the_app_answers_with_a_server_error():
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    Machine(status=502).attach(hub, topic_id)

    assert await hub.probe(topic_id, SEAT) is False


async def test_waiting_for_a_machine_gives_up_rather_than_hanging():
    """`cheese serve` starts the helper and declares the preview in the same
    breath, so the platform waits — but a machine that is not coming must end as
    a refusal the agent can read, not as a request that never returns."""
    hub = PreviewHub()

    assert await hub.wait_online(uuid.uuid4(), SEAT, 0.05) is False


async def test_waiting_ends_the_moment_the_machine_arrives():
    hub = PreviewHub()
    topic_id = uuid.uuid4()

    async def attach_soon():
        await asyncio.sleep(0.01)
        Machine().attach(hub, topic_id)

    waiter = asyncio.create_task(hub.wait_online(topic_id, SEAT, 5.0))
    await attach_soon()

    assert await waiter is True


async def test_another_teammates_arrival_does_not_end_a_wait():
    """The declaring teammate waits for ITS helper; a room-mate's helper coming
    up is not the app it is about to declare."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    waiter = asyncio.create_task(hub.wait_online(topic_id, "cheese-one", 0.1))
    await asyncio.sleep(0.01)
    Machine().attach(hub, topic_id, "cheese-two")

    assert await waiter is False


async def test_headers_and_first_bytes_arrive_before_end_and_cancel_is_once():
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    frames = []

    class Transport:
        async def send_bytes(self, data):
            op, sid, _ = wire.decode(data)
            frames.append(op)
            if op == wire.OP_REQ:
                machine.on_frame(
                    wire.encode(
                        wire.OP_RESP,
                        sid,
                        wire.encode_meta(
                            {
                                "status": 206,
                                "headers": [["content-range", "bytes 0-2/9"]],
                            },
                            b"one",
                        ),
                    )
                )

    machine = hub.attach(topic_id, SEAT, Transport())
    response = await asyncio.wait_for(
        hub.request_stream(topic_id, SEAT, method="GET", path="/stream", headers=[]), 1
    )
    assert response.status == 206
    assert response.headers == [("content-range", "bytes 0-2/9")]
    iterator = response.iter_bytes()
    assert await anext(iterator) == b"one"
    pending = asyncio.create_task(anext(iterator))
    await asyncio.sleep(0)
    assert not pending.done()
    pending.cancel()
    await asyncio.gather(pending, return_exceptions=True)
    await response.aclose()
    assert frames == [wire.OP_REQ, wire.OP_CLOSE]
    assert not machine.streams


async def test_task_cancellation_during_close_still_delivers_bounded_cancel():
    started = asyncio.Event()
    release = asyncio.Event()
    accepted = []

    class Transport:
        async def send_bytes(self, data):
            started.set()
            await release.wait()
            accepted.append(wire.decode(data))

    hub = PreviewHub()
    machine = hub.attach(uuid.uuid4(), SEAT, Transport())
    stream = machine.open()
    closing = asyncio.create_task(stream.aclose())
    await started.wait()
    closing.cancel()
    release.set()
    result = await asyncio.gather(closing, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert accepted == [(wire.OP_CLOSE, stream.id, b"")]
    assert not machine.streams


async def test_repeated_cancellation_reaps_one_deadline_bounded_close(monkeypatch):
    import app.domain.agent.preview_hub as module

    monkeypatch.setattr(module, "SEND_TIMEOUT_S", 0.05)
    started = asyncio.Event()
    writes = []

    class Transport:
        async def send_bytes(self, data):
            writes.append(wire.decode(data))
            started.set()
            await asyncio.Event().wait()

    machine = PreviewHub().attach(uuid.uuid4(), SEAT, Transport())
    stream = machine.open()
    closing = asyncio.create_task(stream.aclose())
    await started.wait()
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    another = asyncio.create_task(stream.aclose())
    results = await asyncio.wait_for(
        asyncio.gather(closing, another, return_exceptions=True), 1
    )
    assert isinstance(results[0], asyncio.CancelledError)
    assert results[1] is None
    assert writes == [(wire.OP_CLOSE, stream.id, b"")]
    assert stream._close_task.done()
    assert not machine.streams


async def test_slow_consumer_is_cancelled_without_blocking_sibling():
    from app.domain.agent.preview_hub import MAX_QUEUED_BYTES

    hub = PreviewHub()
    frames = []

    class Transport:
        async def send_bytes(self, data):
            frames.append(wire.decode(data))

    machine = hub.attach(uuid.uuid4(), SEAT, Transport())
    slow = machine.open()
    sibling = machine.open()
    for _ in range(MAX_QUEUED_BYTES // wire._CHUNK + 1):
        machine.on_frame(wire.encode(wire.OP_DATA, slow.id, b"x" * wire._CHUNK))
    machine.on_frame(wire.encode(wire.OP_DATA, sibling.id, b"sibling"))
    assert await sibling.receive() == (wire.OP_DATA, b"sibling")
    assert (await slow.receive())[0] == wire.OP_CLOSE
    await asyncio.sleep(0)
    assert frames == [(wire.OP_CLOSE, slow.id, b"")]
    sibling.close()
