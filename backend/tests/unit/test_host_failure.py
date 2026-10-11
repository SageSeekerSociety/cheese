"""A person's machine that keeps failing is named and waited for — never
swapped out behind anyone's back.

A failure is counted against the people's machines the room's sessions hold
(a sandbox on the platform's machines is replaced by the pool instead): a
second strike quarantines the machine, and the room is told which one.
"""

import uuid

from app.domain.agent.host_failure import judge_host_failure
from app.domain.agent.platform_failures import (
    RUNTIME_IMAGE_MISSING,
    STORAGE_EXHAUSTED,
)
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply

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


async def _fail(service, held, failure, times=1):
    verdict = None
    for _ in range(times):
        verdict = await judge_host_failure(service, device_ids=held, failure=failure)
    return verdict


async def test_one_strike_is_not_a_verdict():
    service = _service()
    project = uuid.uuid4()
    machine = await _device_on_project(service, project, "机器")

    first = await _fail(service, [machine], STORAGE_EXHAUSTED)
    assert first is not None
    assert not first.quarantined
    assert first.message is None, "one failure is a hiccup, and the room shows it"


async def test_a_dead_machine_is_named_and_waited_for():
    service = _service()
    project = uuid.uuid4()
    own = await _device_on_project(service, project, "自己的机器")
    await _device_on_project(service, project, "另一台")

    verdict = await _fail(service, [own], STORAGE_EXHAUSTED, times=2)

    assert verdict is not None
    assert verdict.quarantined
    assert verdict.device_id == own
    assert verdict.message is not None and "自己的机器" in verdict.message
    # 等这台机器恢复之后，人可以在这条提示上直接重试。
    assert verdict.event_meta["retryable"] is True


async def test_a_failure_that_isnt_the_machines_fault_is_not_recorded():
    service = _service()
    project = uuid.uuid4()
    a = await _device_on_project(service, project, "A")

    verdict = await _fail(service, [a], RUNTIME_IMAGE_MISSING, times=5)
    assert verdict is not None
    assert not verdict.quarantined
    assert await service.host_health(a) is None, "must not even be recorded"


async def test_a_room_holding_no_persons_machine_is_untouched():
    service = _service()

    verdict = await judge_host_failure(
        service, device_ids=[], failure=STORAGE_EXHAUSTED
    )
    assert verdict.message is None and not verdict.quarantined
