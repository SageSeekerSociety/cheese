"""换身体：a topic whose machine died continues on another one (#186).

The rule this must not break, ever: a topic's pin is write-once and the resolver
NEVER falls back to another machine, because a topic that silently woke up
elsewhere with an empty work tree was a real bug nobody could see. So moving a
topic is allowed in exactly one shape — deliberate, reasoned, and announced in the
room. These tests hold that shape, and hold the other half of the fix: after the
move, the turn must actually be rescheduled. A swap nobody continues is a swap
that did nothing.
"""

import uuid

from app.domain.agent.host_swap import swap_topic_device
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
    service: DeviceService, project_id: uuid.UUID, name: str
) -> str:
    code = await service.start(name)
    # `supply`/`visibility` are required with no default (#282 决定 2 / #358): every
    # enrolment site states its own answer. Host swap moves a topic BETWEEN machines
    # that actually run turns, so these enrol as whole-machine (host) — the only
    # transport built today.
    device = await service.approve(
        code,
        owner_user_id=OWNER,
        supply=Supply.self_hosted,
        visibility=Visibility.host,
    )
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


def _online(*ids: str):
    live = set(ids)
    return lambda device_id: device_id in live


async def _fail(service, topic, project, device_ids, failure, times=1):
    outcome = None
    for _ in range(times):
        outcome = await swap_topic_device(
            service,
            topic_id=topic,
            project_id=project,
            failure=failure,
            is_online=_online(*device_ids),
        )
    return outcome


async def test_a_dead_machine_hands_the_topic_to_a_healthy_one_and_says_so():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    sick = await _device_on_project(service, project, "老机器")
    well = await _device_on_project(service, project, "新机器")
    await service.bind_topic_device(topic, sick)

    first = await _fail(service, topic, project, (sick, well), STORAGE_EXHAUSTED)
    assert first is not None and first.new_device is None, "one strike is not a verdict"
    assert await service.topic_device(topic) == sick

    swapped = await _fail(service, topic, project, (sick, well), STORAGE_EXHAUSTED)
    assert swapped is not None
    assert swapped.new_device == well
    assert await service.topic_device(topic) == well, "the pin must actually move"

    # visible, and honest about what a move costs
    assert swapped.message is not None
    assert "老机器" in swapped.message and "新机器" in swapped.message
    assert "丢失" in swapped.message

    # and the whole point: the turn continues by itself
    assert swapped.resume_after_s is not None and swapped.resume_after_s > 0
    assert swapped.resume_reason


async def test_no_healthy_machine_means_stay_put_and_say_why_never_drift():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    only = await _device_on_project(service, project, "唯一一台")
    await service.bind_topic_device(topic, only)

    stuck = await _fail(service, topic, project, (only,), STORAGE_EXHAUSTED, times=2)
    assert stuck is not None
    assert stuck.quarantined
    assert stuck.new_device is None
    assert await service.topic_device(topic) == only, "must not unpin into nowhere"
    assert stuck.message is not None, "being stuck is not allowed to be silent"
    assert stuck.resume_after_s is None, "nothing to resume onto"


async def test_a_failure_that_isnt_the_machines_fault_moves_nothing():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    a = await _device_on_project(service, project, "A")
    await _device_on_project(service, project, "B")
    await service.bind_topic_device(topic, a)

    outcome = await _fail(service, topic, project, (a,), RUNTIME_IMAGE_MISSING, times=5)
    assert outcome is not None
    assert not outcome.quarantined
    assert outcome.new_device is None
    assert await service.topic_device(topic) == a
    assert await service.host_health(a) is None, "must not even be recorded"


async def test_a_topic_not_running_on_an_enrolled_machine_is_untouched():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()

    outcome = await swap_topic_device(
        service,
        topic_id=topic,
        project_id=project,
        failure=STORAGE_EXHAUSTED,
        is_online=_online(),
    )
    assert outcome.new_device is None and outcome.message is None
    assert await service.topic_device(topic) is None


async def test_the_new_machine_is_never_the_one_just_judged_dead():
    """Two strikes on the only online machine, with a second machine that is
    enrolled but offline: the swap must not 'move' the topic back onto the sick
    box just because it is the only one answering."""
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    sick = await _device_on_project(service, project, "sick")
    await _device_on_project(service, project, "offline")
    await service.bind_topic_device(topic, sick)

    outcome = await _fail(service, topic, project, (sick,), STORAGE_EXHAUSTED, times=2)
    assert outcome is not None
    assert outcome.new_device is None
    assert await service.topic_device(topic) == sick
