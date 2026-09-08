"""A machine that keeps failing is named and waited for — never swapped out.

The rule these tests hold: a topic's pin is write-once and nothing moves it.
There used to be a 换身体 path (#186) that replaced a dead cloud machine or
re-pinned the topic onto another one; it is gone, because a swap that works
hides the fault that caused it. What remains is accounting (a second strike
quarantines the machine) and a room-visible verdict that names the machine.
"""

import uuid
from unittest.mock import AsyncMock, patch

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
    supply: Supply = Supply.cloud,
    visibility: Visibility = Visibility.host,
) -> str:
    code = await service.start(name)
    device = await service.approve(
        code,
        owner_user_id=OWNER,
        supply=supply,
        visibility=visibility,
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


async def test_a_dead_cloud_machine_is_named_and_never_replaced():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    sick = await _device_on_project(service, project, "老机器")
    await _device_on_project(service, project, "另一台云机器")
    await service.bind_topic_device(topic, sick, Visibility.host)

    release = AsyncMock(wraps=service.release_topic_device)
    bind = AsyncMock(wraps=service.bind_topic_device)
    with (
        patch.object(service, "release_topic_device", release),
        patch.object(service, "bind_topic_device", bind),
    ):
        verdict = await _fail(service, topic, STORAGE_EXHAUSTED, times=2)

    assert verdict is not None
    assert verdict.quarantined
    assert verdict.device_id == sick
    assert await service.topic_device(topic) == sick
    release.assert_not_awaited()
    bind.assert_not_awaited()
    assert verdict.message is not None and "老机器" in verdict.message
    assert verdict.event_meta is not None
    assert verdict.event_meta["event_type"] == "host_failure"
    # Nothing here may read as "a new machine is on its way": the room's
    # provisioning branch keys on that state, and it would be a lie.
    assert "state" not in verdict.event_meta
    assert "不会换" in verdict.event_meta["detail"]


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
    # 「留在原地等这台机器」是这条通知的全部意义，它在展开区里说。
    assert "等" in verdict.event_meta["detail"]
    assert "不会迁移" in verdict.event_meta["detail"]


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
