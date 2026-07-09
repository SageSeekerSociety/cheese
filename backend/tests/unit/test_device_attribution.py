"""Attribution: device token + screen token → actor (screen = agent, bare = owner)."""

import uuid
from datetime import UTC, datetime

from app.domain.agent.device_attribution import resolve_device_actor
from app.domain.agent.device_hub import DeviceHub, HubScreen
from app.domain.device.memory_repository import InMemoryDeviceRepository
from app.domain.device.repository import Device
from app.domain.device.service import DeviceService


async def _enrolled() -> tuple[DeviceService, Device, uuid.UUID, uuid.UUID]:
    repo = InMemoryDeviceRepository()
    owner, agent = uuid.uuid4(), uuid.uuid4()
    device = Device(
        device_id="d1",
        name="m",
        token="dev-token",
        owner_user_id=owner,
        agent_user_id=agent,
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
    )
    await repo.save_device(device)
    return DeviceService(repo), device, owner, agent


async def _owner_handle(_uid: uuid.UUID) -> str:
    return "owner-handle"


async def test_bare_device_call_acts_as_owner():
    service, _, owner, _agent = await _enrolled()
    hub = DeviceHub()
    attr = await resolve_device_actor(
        service, hub, device_token="dev-token", owner_handle_of=_owner_handle
    )
    assert attr is not None
    assert attr.actor_user_id == owner and attr.actor_handle == "owner-handle"
    assert attr.project_id is None and not attr.inside_screen


async def test_inside_screen_acts_as_agent():
    service, device, _owner, agent = await _enrolled()
    hub = DeviceHub()
    screen = HubScreen(
        sid="s1",
        device_id=device.device_id,
        command=[],
        token="screen-tok",
        agent_user_id=agent,
        agent_handle="agent-x",
        project_id=uuid.uuid4(),
    )
    hub._by_screen_token[screen.token] = screen  # register as if opened

    attr = await resolve_device_actor(
        service,
        hub,
        device_token="dev-token",
        screen_token="screen-tok",
        owner_handle_of=_owner_handle,
    )
    assert attr is not None
    assert attr.actor_user_id == agent and attr.actor_handle == "agent-x"
    assert attr.project_id == screen.project_id and attr.inside_screen


async def test_screen_token_from_another_device_is_ignored():
    service, device, _owner, agent = await _enrolled()
    hub = DeviceHub()
    foreign = HubScreen(
        sid="s2",
        device_id="OTHER-DEVICE",
        command=[],
        token="foreign-tok",
        agent_user_id=uuid.uuid4(),
        agent_handle="foreign",
    )
    hub._by_screen_token[foreign.token] = foreign

    attr = await resolve_device_actor(
        service,
        hub,
        device_token="dev-token",
        screen_token="foreign-tok",  # belongs to a different device → ignored
        owner_handle_of=_owner_handle,
    )
    assert attr is not None
    # Falls back to the device owner, never escalates to the foreign screen's agent.
    assert attr.actor_user_id == device.owner_user_id and not attr.inside_screen


async def test_unknown_device_token_returns_none():
    service, _, _, _ = await _enrolled()
    hub = DeviceHub()
    attr = await resolve_device_actor(
        service, hub, device_token="bogus", owner_handle_of=_owner_handle
    )
    assert attr is None
