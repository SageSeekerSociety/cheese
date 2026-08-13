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
from app.domain.device.supply import Supply, Visibility

OWNER = 1


def _service() -> DeviceService:
    return DeviceService(InMemoryDeviceRepository())


async def _device_on_project(
    service: DeviceService,
    project_id: uuid.UUID,
    name: str,
    visibility: Visibility = Visibility.host,
) -> str:
    # Affinity resolution only ever PINS a device the transport can run today, so
    # these enrol as whole-machine (host) by default; a test that wants to prove an
    # `isolated` device is skipped passes it explicitly.
    code = await service.start(name)
    device = await service.approve(
        code, owner_user_id=OWNER, supply=Supply.self_hosted, visibility=visibility
    )
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


async def test_an_isolated_device_is_never_pinned_on_the_first_turn():
    """#358 gate at the PIN: a machine enrolled as the boxed `isolated` 档 has no
    transport yet, so a fresh topic must NOT freeze to it. With only an isolated
    device online, resolution yields nothing (a clean "no runnable device") rather
    than pinning a topic to a machine that can never run it."""
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    await _device_on_project(service, project, "boxed", visibility=Visibility.isolated)

    dev = await service.list_devices_for_project(project)
    resolved = await resolve_pinned_device(
        service, _online(dev[0].device_id), project, topic
    )
    assert resolved is None
    # ...and nothing was pinned, so the topic is free to land on a whole-machine
    # device later instead of being bricked on the isolated one.
    assert await service.topic_device(topic) is None


async def test_first_turn_pins_the_whole_machine_device_and_skips_the_isolated_one():
    """A mixed project (one `isolated`, one `host`) pins to the whole-machine device
    — the only kind that can run today — never the boxed one."""
    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    boxed = await _device_on_project(
        service, project, "boxed", visibility=Visibility.isolated
    )
    machine = await _device_on_project(
        service, project, "machine", visibility=Visibility.host
    )

    picked = await resolve_pinned_device(
        service, _online(boxed, machine), project, topic
    )
    assert picked == machine
    assert await service.topic_device(topic) == machine


async def test_a_pin_flipped_to_isolated_refuses_rather_than_running_bare():
    """The already-pinned half of the gate: if a topic's pinned machine is later
    re-enrolled as `isolated`, the turn REFUSES (a clear #358 「尚未实现」 error)
    instead of silently launching bare-on-host — the exact whole-machine exposure
    the gate exists to prevent. The pin itself never moves."""
    from app.domain.agent.device_provider import DEVICE_ISOLATED_UNSUPPORTED_MESSAGE

    service = _service()
    project, topic = uuid.uuid4(), uuid.uuid4()
    machine = await _device_on_project(
        service, project, "machine", visibility=Visibility.host
    )
    # Freeze the topic to it while it is whole-machine...
    pinned = await resolve_pinned_device(service, _online(machine), project, topic)
    assert pinned == machine
    # ...then its owner flips it to the boxed 档.
    device = await service.get_device(machine)
    assert device is not None
    device.visibility = Visibility.isolated

    with pytest.raises(ScreenSetupError) as excinfo:
        await resolve_pinned_device(service, _online(machine), project, topic)
    assert str(excinfo.value) == DEVICE_ISOLATED_UNSUPPORTED_MESSAGE
    # The pin is unchanged — the gate refuses, it does not drift.
    assert await service.topic_device(topic) == machine


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
