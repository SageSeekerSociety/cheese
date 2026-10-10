"""「系统挑一台」 does not pick a machine that will refuse the room.

A room's executor runs in a sandbox by default, and a Linux machine a person
enrolled refuses one when it cannot make a bubblewrap sandbox. Picking such a
machine while another could serve the room left the room stopped on a refusal
addressed to the machine's owner.

Driven through ``DeviceHub`` with a fake link and ``DeviceService`` with the
in-memory repo: a machine answers the probe as its connector would.
"""

import asyncio
import uuid
from functools import partial

import pytest

from app.domain.agent.device_hub import DeviceHub
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.service import DeviceService
from app.domain.device.supply import Supply, may_isolate

OWNER = 1


@pytest.fixture(autouse=True)
def _machines_here_do_not_come_back(monkeypatch):
    from app.domain.agent import device_hub

    monkeypatch.setattr(device_hub, "RECONNECT_GRACE_S", 0.0)


class Link:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)

    def asked(self) -> list[dict]:
        return [msg for msg in self.sent if msg["t"] == "exec"]


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def _connect(hub: DeviceHub, device_id: str, target: str) -> Link:
    link = Link()
    await hub.attach_device(device_id, link)
    await hub.on_device_message(
        device_id, {"t": "hello", "v": 1, "target": target, "executor": True}
    )
    await _settle()
    return link


async def _answer(hub: DeviceHub, device_id: str, link: Link, exit_code: int) -> None:
    (probe,) = link.asked()
    await hub.on_device_message(
        device_id,
        {"t": "exec.result", "id": probe["id"], "exit": exit_code, "stderr": ""},
    )
    await _settle()


async def _enrol(service: DeviceService, project_id: uuid.UUID, name: str) -> str:
    device = await service.approve(
        await service.start(name), owner_user_id=OWNER, supply=Supply.self_hosted
    )
    await service.assign_to_project(device.device_id, project_id, actor_user_id=OWNER)
    return device.device_id


async def test_a_linux_machine_is_asked_to_make_a_sandbox_as_it_connects():
    hub = DeviceHub()
    link = await _connect(hub, "box", "linux-amd64")

    (probe,) = link.asked()
    assert probe["command"][0] == "bwrap"
    assert hub.isolates("box") is None
    await _answer(hub, "box", link, 0)
    assert hub.isolates("box") is True


async def test_a_machine_that_cannot_make_one_is_known_as_such():
    hub = DeviceHub()
    link = await _connect(hub, "box", "linux-amd64")
    await _answer(hub, "box", link, -1)
    assert hub.isolates("box") is False


async def test_a_reconnect_asks_again():
    hub = DeviceHub()
    link = await _connect(hub, "box", "linux-amd64")
    await _answer(hub, "box", link, -1)

    again = await _connect(hub, "box", "linux-amd64")
    assert hub.isolates("box") is None
    await _answer(hub, "box", again, 0)
    assert hub.isolates("box") is True


async def test_a_mac_is_not_asked():
    hub = DeviceHub()
    link = await _connect(hub, "mac", "darwin-arm64")
    assert link.asked() == []
    assert hub.isolates("mac") is None


async def test_the_pick_passes_over_a_machine_that_cannot_isolate():
    hub = DeviceHub()
    service = DeviceService(InMemoryDeviceRepository())
    project = uuid.uuid4()
    refusing = await _enrol(service, project, "old box")
    able = await _enrol(service, project, "new box")
    await _answer(hub, refusing, await _connect(hub, refusing, "linux-amd64"), -1)
    await _answer(hub, able, await _connect(hub, able, "linux-amd64"), 0)
    unaware = await service.first_healthy_device(project, hub.is_online)
    assert unaware is not None and unaware.device_id == refusing

    picked = await service.first_healthy_device(
        project, hub.is_online, partial(may_isolate, hub)
    )

    assert picked is not None and picked.device_id == able


async def test_the_pick_passes_over_a_windows_machine():
    hub = DeviceHub()
    service = DeviceService(InMemoryDeviceRepository())
    project = uuid.uuid4()
    windows = await _enrol(service, project, "windows box")
    linux = await _enrol(service, project, "linux box")
    await _connect(hub, windows, "windows-amd64")
    await _connect(hub, linux, "linux-amd64")
    unaware = await service.first_healthy_device(project, hub.is_online)
    assert unaware is not None and unaware.device_id == windows

    picked = await service.first_healthy_device(
        project, hub.is_online, partial(may_isolate, hub)
    )

    assert picked is not None and picked.device_id == linux


async def test_with_no_machine_known_to_isolate_the_first_is_still_picked():
    """Its install then says what it lacks, and a machine whose owner has just
    installed bubblewrap is tried rather than refused on its old answer."""
    hub = DeviceHub()
    service = DeviceService(InMemoryDeviceRepository())
    project = uuid.uuid4()
    only = await _enrol(service, project, "old box")
    await _answer(hub, only, await _connect(hub, only, "linux-amd64"), -1)

    picked = await service.first_healthy_device(
        project, hub.is_online, partial(may_isolate, hub)
    )

    assert picked is not None and picked.device_id == only
