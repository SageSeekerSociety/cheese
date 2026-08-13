"""Compute affinity (execution-architecture v4 §affinity).

A topic's work tree + resumable claude session live on ONE self-hosted machine, so a
topic freezes to the device its first turn ran on and every later turn returns to the
SAME device — it must NEVER drift to another online device (that would start from an
empty tree and corrupt session resume: the original PR-#46 bug).
``resolve_pinned_device`` is the pure resolution ``DeviceProvider`` runs each turn;
these test it directly with the in-memory repo (no DB, no hub).
"""

import uuid

import pytest

from app.domain.agent.device_provider import resolve_pinned_device
from app.domain.agent.hooks_substrate import ScreenSetupError
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply

OWNER = 1


def _service() -> DeviceService:
    return DeviceService(InMemoryDeviceRepository())


async def _device_on_project(
    service: DeviceService, project_id: uuid.UUID, name: str
) -> str:
    code = await service.start(name)
    device = await service.approve(code, owner_user_id=OWNER, supply=Supply.self_hosted)
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


def _online(*ids: str):
    live = set(ids)
    return lambda device_id: device_id in live


async def test_first_turn_pins_to_an_online_device():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    dev = await _device_on_project(service, project, "box")

    picked = await resolve_pinned_device(service, _online(dev), project, topic)
    assert picked == dev
    # the pin is now durable — recorded on the topic
    assert await service.topic_device(topic) == dev


async def test_later_turns_return_to_the_pinned_device_never_drift():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    dev_a = await _device_on_project(service, project, "A")
    dev_b = await _device_on_project(service, project, "B")

    # first turn: only A online → pins to A
    assert await resolve_pinned_device(service, _online(dev_a), project, topic) == dev_a
    # next turn: B ALSO online, A still online → still A (pinned), never drifts to B
    assert (
        await resolve_pinned_device(service, _online(dev_a, dev_b), project, topic)
        == dev_a
    )


async def test_pinned_device_offline_raises_and_does_not_drift():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    dev_a = await _device_on_project(service, project, "A")
    dev_b = await _device_on_project(service, project, "B")

    await resolve_pinned_device(service, _online(dev_a), project, topic)  # pin A

    # A offline, B online → must RAISE (queue/retry), never silently run on B
    with pytest.raises(ScreenSetupError):
        await resolve_pinned_device(service, _online(dev_b), project, topic)
    # pin unchanged — the topic still belongs to A
    assert await service.topic_device(topic) == dev_a


async def test_no_online_device_returns_none_and_pins_nothing():
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    await _device_on_project(service, project, "box")  # assigned but offline

    assert await resolve_pinned_device(service, _online(), project, topic) is None
    assert await service.topic_device(topic) is None


async def test_project_device_online_is_scoped_to_the_project_context():
    # The self-hosted compute pool is available for a project only when THAT
    # project has an online enrolled machine — compute belongs to the context,
    # not globally (v4). A device online but assigned elsewhere doesn't count.
    service = _service()
    project_a, project_b = uuid.uuid4(), uuid.uuid4()
    dev_a = await _device_on_project(service, project_a, "A")

    # A's device online → available for A, but NOT for B (which has no device).
    assert await service.project_has_online_device(project_a, _online(dev_a)) is True
    assert await service.project_has_online_device(project_b, _online(dev_a)) is False
    # Assigned but offline → not available.
    assert await service.project_has_online_device(project_a, _online()) is False
