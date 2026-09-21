"""Deliver the message a Cloud topic has been holding, the moment its machine can.

A topic that picked Cloud sends its first message into a room with no machine
yet: the message is kept (never consumed) and the room shows 「机器正在创建」.
Two facts have to hold before it can be delivered, and they live in different
places — the machine has settled both provider lifecycles and is enrolled (a
database fact), and its connector is connected right now (an in-memory fact
only the hub knows). This is the one place that joins the two and delivers the
held message.

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

#: 送过去的那一句。**它不是平台起的一轮**：房间扣着的那条人写的消息才是这一轮的
#: 内容（待读窗口会把它原样接上），这一句只说明为什么现在才送到。收件人是这个房间
#: 的芝士席位，点它名的是当初发那条消息的人（I12）。
WAKE_PROMPT = "Cloud machine is ready; continue the pending input."
#: 房间里看得见的那一行 —— 送达这一轮的开场白，同时是这个房间的 Cloud 生命周期
#: 转出「正在创建」的那条记录。一件事一条记录：它由送消息的那一轮写，不再有第二
#: 次广播把同一句话在房间里说第二遍。
WAKE_NOTICE = "Cloud 机器已接入，正在继续刚才的消息"

ReadyLeases = Callable[[str], Awaitable[list[tuple[uuid.UUID, str]]]]
WaitingTopics = Callable[[list[uuid.UUID]], Awaitable[list[uuid.UUID]]]
TopicAction = Callable[[uuid.UUID], Awaitable[None]]
FailureAction = Callable[[uuid.UUID, str], Awaitable[None]]


class CloudWakeup:
    def __init__(
        self,
        *,
        ready_leases: ReadyLeases,
        waiting_topics: WaitingTopics,
        deliver_held: TopicAction,
        is_online: Callable[[str], bool],
        announce_failure: FailureAction | None = None,
    ) -> None:
        self._ready_leases = ready_leases
        self._waiting_topics = waiting_topics
        self._deliver_held = deliver_held
        self._is_online = is_online
        self._announce_failure = announce_failure

    async def wake(self, ready: list[tuple[uuid.UUID, str]]) -> None:
        """Deliver the held message of every lease whose machine is connected
        and whose room is still showing the waiting state. Level-triggered and
        idempotent: a topic already told 「已接入」 is not woken twice —— 那一行
        是送达那一轮的开场白，落库之后这个房间就不再「正在创建」了。"""
        topic_ids = [
            topic_id for topic_id, device_id in ready if self._is_online(device_id)
        ]
        if not topic_ids:
            return
        for topic_id in await self._waiting_topics(topic_ids):
            await self._deliver_held(topic_id)
            logger.info("cloud topic %s woken: its machine is connected", topic_id)

    async def report_failures(self, failed: list) -> None:
        """Tell every room still waiting on a lease that MicroCloud has given up
        on that it is not coming. Once: a room whose latest Cloud event is no
        longer 「waiting」 has already been told. No retry and no other machine —
        the topic stays on the failed lease until a person has looked."""
        if self._announce_failure is None or not failed:
            return
        by_topic = {lease.topic_id: lease for lease in failed}
        for topic_id in await self._waiting_topics(list(by_topic)):
            lease = by_topic[topic_id]
            await self._announce_failure(
                topic_id, f"Cloud 机器「{lease.hostname}」没有起来：{lease.reason}"
            )
            logger.warning(
                "cloud topic %s told its machine %s failed: %s",
                topic_id,
                lease.hostname,
                lease.reason,
            )

    async def wake_device(self, device_id: str) -> None:
        """The connector of ``device_id`` just attached: if a settled lease is
        waiting on exactly this machine, deliver now rather than at the next
        sweep tick."""
        await self.wake(await self._ready_leases(device_id))
