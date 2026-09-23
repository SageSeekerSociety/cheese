import asyncio
import uuid
from types import SimpleNamespace

import pytest

from app.domain.agent.chat import ChatService
from tests.conftest import settle_turn


class _Runtime:
    def __init__(self, topic_id, consumer):
        runtime = self

        class _RetiringQueue:
            async def join(self):
                runtime._subscriptions.pop(topic_id)

        self._subscriptions = {
            topic_id: SimpleNamespace(
                consumer_task=consumer,
                sink=SimpleNamespace(queue=_RetiringQueue()),
            )
        }

    async def _close_topic(self, topic_id):
        self._subscriptions.pop(topic_id)


def _service(*runtimes, settle_tasks=()):
    return SimpleNamespace(
        _compute=SimpleNamespace(_runtimes=lambda: runtimes),
        _hook_work=set(),
        _settle_tasks=set(settle_tasks),
    )


@pytest.mark.anyio
async def test_settle_turn_propagates_a_retired_consumer_failure():
    topic_id = uuid.uuid4()

    async def fail():
        await asyncio.sleep(0)
        raise RuntimeError("consumer failed")

    consumer = asyncio.create_task(fail())
    service = _service(_Runtime(topic_id, consumer))

    with pytest.raises(RuntimeError, match="consumer failed"):
        await settle_turn(service, topic_id)


@pytest.mark.anyio
async def test_settle_turn_waits_only_for_its_topics_reconciliation():
    topic_id = uuid.uuid4()
    other_topic_id = uuid.uuid4()
    other_release = asyncio.Event()

    async def reconcile(for_topic):
        if for_topic == other_topic_id:
            await other_release.wait()
        return 0

    service = ChatService.__new__(ChatService)
    service._compute = SimpleNamespace(_runtimes=lambda: ())
    service._hook_work = set()
    service._settle_pending = set()
    service._settle_tasks = set()
    service.settle_spool = reconcile
    service.schedule_spool_settle(topic_id, delay_s=0)
    service.schedule_spool_settle(other_topic_id, delay_s=0)
    other = next(
        task
        for task in service._settle_tasks
        if task.get_coro().cr_frame.f_locals["topic_id"] == other_topic_id
    )

    try:
        await settle_turn(service, topic_id)
        assert not other.done()
    finally:
        other.cancel()
        with pytest.raises(asyncio.CancelledError):
            await other
