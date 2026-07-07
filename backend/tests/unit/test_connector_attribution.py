"""Composition test: the full connector attribution chain (Act 2 steps 1+2+5).

Proves the two transport-free cores join correctly — a device enrolled to an owner,
a screen (agent) opened on it in a project, and a ``cheese api`` call resolving:
inside a screen it acts as that screen's agent user in its project; outside a
screen it acts as the device's human owner. No WebSocket, no DB, no real device.
"""

import pytest

from app.agent.attribution import resolve_actor
from app.agent.hub import DeviceHub
from app.domain.device import DeviceService, InMemoryDeviceRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeDevice:
    async def send_json(self, msg: dict) -> None:  # type: ignore[type-arg]
        pass


async def enroll(svc: DeviceService, *, owner: int) -> str:
    code = await svc.start("machine")
    device = await svc.approve(code, actor_user_id=owner)
    return device.token


async def test_call_inside_a_screen_acts_as_the_screens_agent() -> None:
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    token = await enroll(svc, owner=5)
    device = await svc.verify_token(token)
    assert device is not None

    await hub.attach_device(device.device_id, FakeDevice())
    screen = await hub.open_screen(device.device_id, ["claude"], "src", project_id=7, agent_user_id=99)

    actor = await resolve_actor(svc, hub, device_token=token, screen_token=screen.token)
    assert actor is not None
    assert actor.actor_user_id == 99  # the screen's agent, not the device owner
    assert actor.owner_user_id == 5
    assert actor.project_id == 7
    assert actor.inside_screen
    assert actor.screen is screen


async def test_call_outside_a_screen_acts_as_the_device_owner() -> None:
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    token = await enroll(svc, owner=55)

    actor = await resolve_actor(svc, hub, device_token=token, screen_token=None)
    assert actor is not None
    assert actor.actor_user_id == 55  # the device owner
    assert actor.owner_user_id == 55
    assert actor.project_id is None
    assert not actor.inside_screen


async def test_unknown_device_token_resolves_to_nobody() -> None:
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    assert (await resolve_actor(svc, hub, device_token="garbage")) is None
    assert (await resolve_actor(svc, hub, device_token="")) is None


async def test_screen_token_from_another_device_is_never_trusted() -> None:
    # A screen token that belongs to device B must not scope a call authenticated
    # as device A — it is ignored, so the call falls back to device A's owner.
    svc = DeviceService(InMemoryDeviceRepository())
    hub = DeviceHub()
    token_a = await enroll(svc, owner=10)
    token_b = await enroll(svc, owner=20)
    dev_a = await svc.verify_token(token_a)
    dev_b = await svc.verify_token(token_b)
    assert dev_a is not None and dev_b is not None

    await hub.attach_device(dev_b.device_id, FakeDevice())
    screen_b = await hub.open_screen(dev_b.device_id, ["x"], "src", project_id=2, agent_user_id=222)

    actor = await resolve_actor(svc, hub, device_token=token_a, screen_token=screen_b.token)
    assert actor is not None
    assert actor.actor_user_id == 10  # falls back to device A's owner
    assert actor.screen is None
    assert not actor.inside_screen
