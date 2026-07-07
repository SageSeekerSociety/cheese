"""Unit tests for the connector device flow (``domain/device``, Act 2 step 1).

Exercised against the in-memory repository — no DB, no WebSocket. These guard the
contract the frozen ``cheese`` CLI speaks (start → approve → poll → durable token)
plus the invariants the real backend adds over the web-claude demo: approval binds
the device to its **owner** (the approving human), and a token only *identifies* a
device. Project assignment and per-screen agent identity are separate concerns.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.core.errors import BadRequestError, NotFoundError
from app.domain.device import DeviceService, DeviceStatus, InMemoryDeviceRepository

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def make_service(
    now: datetime | None = None,
) -> tuple[DeviceService, dict[str, datetime]]:
    clock = {"t": now or datetime(2026, 7, 7, tzinfo=UTC)}
    svc = DeviceService(InMemoryDeviceRepository(), now=lambda: clock["t"])
    return svc, clock


async def test_full_flow_start_approve_poll() -> None:
    svc, _ = make_service()
    code = await svc.start("laptop")

    # Before approval, poll reveals only the pending status — no token leaks.
    pending = await svc.poll(code)
    assert pending == {"status": DeviceStatus.PENDING}

    device = await svc.approve(code, actor_user_id=42)
    assert device.owner_user_id == 42
    assert device.name == "laptop"
    assert device.token

    approved = await svc.poll(code)
    assert approved["status"] == DeviceStatus.APPROVED
    assert approved["token"] == device.token
    assert approved["device_id"] == device.device_id
    assert approved["device_name"] == "laptop"


async def test_approve_is_idempotent() -> None:
    svc, _ = make_service()
    code = await svc.start("box")
    d1 = await svc.approve(code, actor_user_id=1)
    d2 = await svc.approve(code, actor_user_id=1)
    assert d1.device_id == d2.device_id
    assert d1.token == d2.token  # no second token minted


async def test_unknown_code_is_rejected() -> None:
    svc, _ = make_service()
    with pytest.raises(NotFoundError):
        await svc.poll("does-not-exist")
    with pytest.raises(NotFoundError):
        await svc.approve("nope", actor_user_id=1)


async def test_expired_code_is_rejected() -> None:
    svc, clock = make_service()
    code = await svc.start("slow")
    clock["t"] = clock["t"] + timedelta(minutes=11)  # past the 10-minute TTL
    with pytest.raises(NotFoundError):
        await svc.poll(code)
    with pytest.raises(NotFoundError):
        await svc.approve(code, actor_user_id=1)


async def test_default_device_name() -> None:
    svc, _ = make_service()
    code = await svc.start(None)
    device = await svc.approve(code, actor_user_id=1)
    assert device.name == "unnamed"
    code2 = await svc.start("   ")
    device2 = await svc.approve(code2, actor_user_id=1)
    assert device2.name == "unnamed"


async def test_verify_and_resolve_owner() -> None:
    svc, _ = make_service()
    code = await svc.start("m")
    device = await svc.approve(code, actor_user_id=77)

    assert (await svc.verify_token(device.token)) is not None
    assert await svc.resolve_owner(device.token) == 77
    # An empty or unknown token identifies nobody.
    assert (await svc.verify_token("")) is None
    assert (await svc.verify_token("garbage")) is None
    with pytest.raises(NotFoundError):
        await svc.resolve_owner("garbage")


async def test_get_device() -> None:
    svc, _ = make_service()
    device = await svc.approve(await svc.start("m"), actor_user_id=5)
    fetched = await svc.get_device(device.device_id)
    assert fetched is not None and fetched.owner_user_id == 5
    assert (await svc.get_device("nope")) is None


async def test_rename() -> None:
    svc, _ = make_service()
    code = await svc.start("old")
    device = await svc.approve(code, actor_user_id=1)
    renamed = await svc.rename(device.token, "new-name")
    assert renamed.name == "new-name"
    assert await svc.resolve_owner(device.token) == 1  # same device
    with pytest.raises(BadRequestError):
        await svc.rename(device.token, "   ")
    with pytest.raises(NotFoundError):
        await svc.rename("garbage", "x")


async def test_two_devices_have_distinct_tokens_and_owners() -> None:
    svc, _ = make_service()
    a = await svc.approve(await svc.start("a"), actor_user_id=10)
    b = await svc.approve(await svc.start("b"), actor_user_id=20)
    assert a.token != b.token
    assert a.device_id != b.device_id
    assert await svc.resolve_owner(a.token) == 10
    assert await svc.resolve_owner(b.token) == 20
