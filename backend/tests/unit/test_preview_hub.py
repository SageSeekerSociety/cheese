"""运行环境预览's platform end: which machine a topic's preview reaches, and what
happens when that answer changes under it.

I/O-free, like the device hub it is modelled on — the transport is a Protocol, so
a fake with a list in it is a complete machine.
"""

import asyncio
import uuid

from app.domain.agent import preview_tunnel as wire
from app.domain.agent.preview_hub import PreviewHub


class Machine:
    """Answers every request with one canned response; records what it was asked."""

    def __init__(self, hub: PreviewHub, topic_id: uuid.UUID, *, status: int = 200):
        self.hub = hub
        self.topic_id = topic_id
        self.status = status
        self.asked: list[str] = []

    async def send_bytes(self, data: bytes) -> None:
        op, stream, payload = wire.decode(data)
        if op != wire.OP_REQ:
            return
        meta, _ = wire.decode_meta(payload)
        self.asked.append(meta["path"])
        self.hub.on_frame(
            self.topic_id,
            wire.encode(
                wire.OP_RESP,
                stream,
                wire.encode_meta({"status": self.status, "headers": []}, b"body"),
            ),
        )
        self.hub.on_frame(self.topic_id, wire.encode(wire.OP_END, stream))


class SilentMachine:
    """Connected, and never answers — an app that died behind a live tunnel."""

    def __init__(self) -> None:
        self.asked = 0

    async def send_bytes(self, data: bytes) -> None:
        self.asked += 1


async def test_a_request_reaches_the_topics_machine_and_comes_back():
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    hub.attach(topic_id, Machine(hub, topic_id))

    response = await hub.request(topic_id, method="GET", path="/index.html", headers=[])

    assert response is not None
    assert response.status == 200 and response.body == b"body"


async def test_a_topic_with_no_machine_is_a_miss_not_a_hang():
    hub = PreviewHub()

    assert await hub.request(uuid.uuid4(), method="GET", path="/", headers=[]) is None


async def test_the_newest_machine_owns_the_topic():
    """A topic runs on one machine at a time, and it moves — a relaunched screen,
    a Cloud box replaced. Keeping the old connection would leave the room looking
    at a machine the work left, with nothing saying which one it is."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    first = Machine(hub, topic_id)
    hub.attach(topic_id, first)
    second = Machine(hub, topic_id)
    hub.attach(topic_id, second)

    await hub.request(topic_id, method="GET", path="/", headers=[])

    assert second.asked == ["/"] and first.asked == []


async def test_the_loser_of_a_replacement_cannot_evict_the_winner():
    """Both connections tear down eventually, and the old one's teardown lands
    after the new one attached. Detaching by topic alone would take the live
    machine with it and the preview would die a moment after being restored."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    first = Machine(hub, topic_id)
    hub.attach(topic_id, first)
    second = Machine(hub, topic_id)
    hub.attach(topic_id, second)

    hub.detach(topic_id, first)

    assert hub.is_online(topic_id)


async def test_a_machine_going_away_ends_what_was_waiting_on_it():
    """A proxied WebSocket waits with no deadline — an HMR socket is silent until
    somebody edits a file. So the machine's disappearance has to arrive as a
    frame, or every viewer's browser socket is held open forever with nothing
    ever coming to end it."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    machine = SilentMachine()
    hub.attach(topic_id, machine)
    stream = hub.open_stream(topic_id)
    assert stream is not None

    hub.detach(topic_id, machine)

    op, _payload = await stream.receive(timeout=1.0)
    assert op == wire.OP_CLOSE


async def test_probe_is_false_when_the_app_never_answers():
    """A connected helper is not a running app. Reporting `online` off the
    connection alone is what embeds an iframe that can only render a white box."""
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    machine = SilentMachine()
    hub.attach(topic_id, machine)

    assert await hub.probe(topic_id, timeout=0.05) is False
    assert machine.asked == 1, "it has to actually knock, not read a flag"


async def test_probe_is_false_when_the_app_answers_with_a_server_error():
    hub = PreviewHub()
    topic_id = uuid.uuid4()
    hub.attach(topic_id, Machine(hub, topic_id, status=502))

    assert await hub.probe(topic_id) is False


async def test_waiting_for_a_machine_gives_up_rather_than_hanging():
    """`cheese serve` starts the helper and declares the preview in the same
    breath, so the platform waits — but a machine that is not coming must end as
    a refusal the agent can read, not as a request that never returns."""
    hub = PreviewHub()

    assert await hub.wait_online(uuid.uuid4(), 0.05) is False


async def test_waiting_ends_the_moment_the_machine_arrives():
    hub = PreviewHub()
    topic_id = uuid.uuid4()

    async def attach_soon():
        await asyncio.sleep(0.01)
        hub.attach(topic_id, Machine(hub, topic_id))

    waiter = asyncio.create_task(hub.wait_online(topic_id, 5.0))
    await attach_soon()

    assert await waiter is True
