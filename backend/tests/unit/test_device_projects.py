"""Unit tests for device↔project assignment (``DeviceService``, in-memory repo).

The owner assigns a device to the projects it may run agents in; only the owner
may manage them; assignment is idempotent; ``serves_project`` gates where agents
may run.
"""

import pytest

from app.core.errors import ForbiddenError, NotFoundError
from app.domain.device import DeviceService, InMemoryDeviceRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _enrolled(svc: DeviceService, owner: int) -> str:
    device = await svc.approve(await svc.start("m"), actor_user_id=owner)
    return device.device_id


async def test_owner_assigns_and_lists_projects() -> None:
    svc = DeviceService(InMemoryDeviceRepository())
    device_id = await _enrolled(svc, owner=7)

    assert await svc.list_projects(device_id) == []
    assert await svc.serves_project(device_id, 100) is False

    await svc.assign_to_project(device_id, 100, actor_user_id=7)
    await svc.assign_to_project(device_id, 200, actor_user_id=7)
    await svc.assign_to_project(device_id, 100, actor_user_id=7)  # idempotent

    assert await svc.list_projects(device_id) == [100, 200]
    assert await svc.serves_project(device_id, 100) is True

    await svc.unassign_from_project(device_id, 100, actor_user_id=7)
    assert await svc.list_projects(device_id) == [200]
    assert await svc.serves_project(device_id, 100) is False


async def test_only_the_owner_may_assign() -> None:
    svc = DeviceService(InMemoryDeviceRepository())
    device_id = await _enrolled(svc, owner=7)
    with pytest.raises(ForbiddenError):
        await svc.assign_to_project(device_id, 100, actor_user_id=999)
    with pytest.raises(ForbiddenError):
        await svc.unassign_from_project(device_id, 100, actor_user_id=999)


async def test_assign_unknown_device_is_404() -> None:
    svc = DeviceService(InMemoryDeviceRepository())
    with pytest.raises(NotFoundError):
        await svc.assign_to_project("ghost", 100, actor_user_id=1)
