"""Deliver the message a Cloud topic has been holding, the moment its machine can.

A topic that picked Cloud sends its first message into a room with no machine
yet: the message is kept (never consumed) and the room shows 「机器正在创建」.
Two facts have to hold before it can be delivered, and they live in different
places — the machine has settled both provider lifecycles and is enrolled (a
database fact), and its connector is connected right now (an in-memory fact
only the hub knows). This is the one place that joins the two and starts the
turn that delivers the held message.

It is asked from two directions, because either fact can be the last one to
arrive: the enrollment sweep (``wake``) with every settled lease after it has
enrolled machines, and the connector route (``wake_device``) the moment a
device attaches. The device is usually the fact that arrives last, and
waiting for the next sweep tick to notice it was 53 s of a 4½-minute first
turn (dev, 2026-09-02).
"""

import logging
import uuid
from collections.abc import Awaitable, Callable

logger = logging.getLogger("cheese.machine.wakeup")

#: What the delivering turn is told. No human wrote it, and the pending human
#: message rides in with it — the prompt only has to say why the turn exists.
WAKE_PROMPT = "Cloud machine is ready; continue the pending input."
WAKE_NOTICE = "Cloud 机器已接入，正在继续刚才的消息"

ReadyLeases = Callable[[str], Awaitable[list[tuple[uuid.UUID, str]]]]
WaitingTopics = Callable[[list[uuid.UUID]], Awaitable[list[uuid.UUID]]]
TopicAction = Callable[[uuid.UUID], Awaitable[None]]


class CloudWakeup:
    def __init__(
        self,
        *,
        ready_leases: ReadyLeases,
        waiting_topics: WaitingTopics,
        kickoff: TopicAction,
        announce: TopicAction,
        is_online: Callable[[str], bool],
    ) -> None:
        self._ready_leases = ready_leases
        self._waiting_topics = waiting_topics
        self._kickoff = kickoff
        self._announce = announce
        self._is_online = is_online

    async def wake(self, ready: list[tuple[uuid.UUID, str]]) -> None:
        """Start the delivering turn for every lease whose machine is connected
        and whose room is still showing the waiting state. Level-triggered and
        idempotent: a topic already told 「已接入」 is not woken twice."""
        topic_ids = [
            topic_id for topic_id, device_id in ready if self._is_online(device_id)
        ]
        if not topic_ids:
            return
        for topic_id in await self._waiting_topics(topic_ids):
            await self._kickoff(topic_id)
            await self._announce(topic_id)
            logger.info("cloud topic %s woken: its machine is connected", topic_id)

    async def wake_device(self, device_id: str) -> None:
        """The connector of ``device_id`` just attached: if a settled lease is
        waiting on exactly this machine, deliver now rather than at the next
        sweep tick."""
        await self.wake(await self._ready_leases(device_id))
