"""A machine that keeps failing is named and waited for — never swapped out.

The rule these tests hold: a topic's pin is write-once and nothing moves it.
There used to be a 换身体 path (#186) that re-pinned the topic onto another
machine; it is gone, because a swap that works hides the fault that caused
it. A cloud sandbox is no pin at all: the pool replaces a broken one. What
remains is accounting (a second strike quarantines the machine) and a
room-visible verdict that names the machine.
"""

import uuid

from app.domain.agent.host_failure import judge_host_failure
from app.domain.agent.platform_failures import (
    RUNTIME_IMAGE_MISSING,
    STORAGE_EXHAUSTED,
)
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply, Visibility

OWNER = 1


def _service() -> DeviceService:
    return DeviceService(InMemoryDeviceRepository())


async def _device_on_project(
    service: DeviceService,
    project_id: uuid.UUID,
    name: str,
    *,
    supply: Supply = Supply.self_hosted,
) -> str:
    code = await service.start(name)
    device = await service.approve(
        code,
        owner_user_id=OWNER,
        supply=supply,
    )
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


async def _fail(service, topic, failure, times=1):
    verdict = None
    for _ in range(times):
        verdict = await judge_host_failure(service, topic_id=topic, failure=failure)
    return verdict


async def test_one_strike_is_not_a_verdict():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    machine = await _device_on_project(service, project, "机器")
    await service.bind_topic_device(topic, machine, Visibility.host)

    first = await _fail(service, topic, STORAGE_EXHAUSTED)
    assert first is not None
    assert not first.quarantined
    assert first.message is None, "one failure is a hiccup, and the room shows it"
    assert await service.topic_device(topic) == machine


async def test_a_dead_self_hosted_machine_keeps_its_pin_and_waits_for_it():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    own = await _device_on_project(
        service, project, "自己的机器", supply=Supply.self_hosted
    )
    await _device_on_project(service, project, "云机器")
    await service.bind_topic_device(topic, own, Visibility.host)

    verdict = await _fail(service, topic, STORAGE_EXHAUSTED, times=2)

    assert verdict is not None
    assert verdict.quarantined
    assert await service.topic_device(topic) == own
    assert verdict.message is not None and "自己的机器" in verdict.message
    # 等这台机器恢复之后，人可以在这条提示上直接重试。
    assert verdict.event_meta["retryable"] is True


async def test_a_failure_that_isnt_the_machines_fault_is_not_recorded():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    a = await _device_on_project(service, project, "A")
    await service.bind_topic_device(topic, a, Visibility.host)

    verdict = await _fail(service, topic, RUNTIME_IMAGE_MISSING, times=5)
    assert verdict is not None
    assert not verdict.quarantined
    assert await service.topic_device(topic) == a
    assert await service.host_health(a) is None, "must not even be recorded"


async def test_a_topic_not_running_on_an_enrolled_machine_is_untouched():
    service = _service()
    topic = uuid.uuid4()

    verdict = await judge_host_failure(
        service, topic_id=topic, failure=STORAGE_EXHAUSTED
    )
    assert verdict.message is None and not verdict.quarantined
    assert await service.topic_device(topic) is None
