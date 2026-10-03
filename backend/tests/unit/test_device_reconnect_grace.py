"""A machine whose link blinks is on its way back, not offline.

A connector redials within seconds of losing its link. A call made in that
window is delivered once the machine has said hello again; only a machine that
stays away past the window, or that this process never saw, is answered as
offline — and then exactly as before, so a caller can still tell a call that
never left from one whose outcome is unknown."""

import asyncio

import pytest

from app.domain.agent import device_hub
from app.domain.agent.device_hub import DeviceHub, DeviceOffline, DeviceUnreachable


class Link:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.sent.append(msg)


def frames(link: Link, kind: str) -> list[dict]:
    return [m for m in link.sent if m["t"] == kind]


async def linked_machine(hub: DeviceHub) -> Link:
    link = Link()
    await hub.attach_device("dev", link)
    await hub.on_device_message("dev", {"t": "hello", "executor": True})
    return link


async def test_a_command_issued_while_the_link_blinks_runs_once_it_is_back():
    hub = DeviceHub()
    old = await linked_machine(hub)
    await hub.detach_device("dev", old)

    command = asyncio.create_task(hub.exec("dev", ["pwd"], timeout=5))
    await asyncio.sleep(0.05)
    assert not command.done(), "a machine that just dropped is not yet offline"

    new = await linked_machine(hub)
    await asyncio.sleep(0)
    [sent] = frames(new, "exec")
    assert sent["command"] == ["pwd"]
    assert frames(old, "exec") == []
    await hub.on_device_message(
        "dev", {"t": "exec.result", "id": sent["id"], "stdout": "/home", "exit": 0}
    )
    assert (await asyncio.wait_for(command, 1))["stdout"] == "/home"


async def test_a_tool_call_waits_for_the_machine_to_say_it_can_run_tools():
    """Reattaching alone does not make a machine able to run a tool call: what
    it can do is in its hello. A call that waited out the reconnect must not
    then be refused for a machine that is about to say it is ready."""
    hub = DeviceHub()
    old = await linked_machine(hub)
    await hub.detach_device("dev", old)

    call = asyncio.create_task(hub.call_executor("dev", "/state", "ping", {}))
    new = Link()
    await hub.attach_device("dev", new)
    await asyncio.sleep(0.05)
    assert not call.done()
    await hub.on_device_message("dev", {"t": "hello", "executor": True})
    await asyncio.sleep(0)
    assert len(frames(new, "execution.call")) == 1
    call.cancel()


async def test_a_machine_that_does_not_come_back_is_offline_when_the_window_ends(
    monkeypatch,
):
    monkeypatch.setattr(device_hub, "RECONNECT_GRACE_S", 0.2)
    hub = DeviceHub()
    link = await linked_machine(hub)
    await hub.detach_device("dev", link)

    with pytest.raises(DeviceOffline):
        await asyncio.wait_for(hub.exec("dev", ["pwd"], timeout=5), 1)
    # A tool call that never left is still known never to have left, which is
    # what lets the platform record it as not having happened.
    with pytest.raises(DeviceUnreachable):
        await asyncio.wait_for(hub.call_executor("dev", "/state", "ping", {}), 1)
    assert frames(link, "exec") == frames(link, "execution.call") == []


async def test_a_machine_away_longer_than_the_window_is_offline_at_once(monkeypatch):
    monkeypatch.setattr(device_hub, "RECONNECT_GRACE_S", 0.1)
    hub = DeviceHub()
    link = await linked_machine(hub)
    await hub.detach_device("dev", link)
    await asyncio.sleep(0.15)

    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(DeviceOffline):
        await hub.exec("dev", ["pwd"], timeout=5)
    assert loop.time() - started < 0.05


async def test_a_machine_this_server_never_saw_is_offline_at_once():
    hub = DeviceHub()
    loop = asyncio.get_running_loop()
    started = loop.time()
    with pytest.raises(DeviceOffline):
        await hub.exec("elsewhere", ["pwd"], timeout=5)
    assert loop.time() - started < 0.05


async def test_a_call_in_flight_when_the_link_drops_is_not_sent_again():
    """The machine may have run it. Delivering it again on the new link would
    run it twice; the caller hears that the link dropped under it."""
    hub = DeviceHub()
    old = await linked_machine(hub)
    command = asyncio.create_task(hub.exec("dev", ["touch", "x"], timeout=5))
    await asyncio.sleep(0)
    assert len(frames(old, "exec")) == 1

    await hub.detach_device("dev", old)
    new = await linked_machine(hub)

    with pytest.raises(DeviceOffline):
        await asyncio.wait_for(command, 1)
    assert frames(new, "exec") == []


async def test_the_backend_sees_a_machine_on_its_way_back(monkeypatch):
    """The backend decides from the owner's snapshot whether to call a machine
    at all; a machine that just dropped must not read there as away."""
    import httpx

    from app.core.config import settings
    from app.device_connection_app import app as owner_app
    from app.domain.agent.device_hub import device_hub as owner_hub
    from app.domain.agent.device_hub_rpc import RemoteDeviceHub

    monkeypatch.setattr(settings, "device_connection_secret", "test-owner-secret")
    monkeypatch.setattr(device_hub, "RECONNECT_GRACE_S", 0.2)
    backend = RemoteDeviceHub(
        "http://owner",
        settings.device_connection_auth_secret,
        transport=httpx.ASGITransport(app=owner_app),
    )
    link = Link()
    await owner_hub.attach_device("snapshot-machine", link)
    try:
        await owner_hub.detach_device("snapshot-machine", link)
        await backend.refresh()
        assert not backend.is_online("snapshot-machine")
        assert backend.reconnecting("snapshot-machine")

        await asyncio.sleep(0.25)
        await backend.refresh()
        assert not backend.reconnecting("snapshot-machine")
    finally:
        owner_hub._devices.pop("snapshot-machine", None)
        await backend.close()
