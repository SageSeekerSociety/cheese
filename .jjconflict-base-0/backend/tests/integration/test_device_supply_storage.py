"""供给形式落到存储 (#282 决定 2): the two new `device` columns survive a real
round-trip through Postgres, and the platform's reclaim door reads the stored
value rather than any join.

The unit tests cover the rule; this covers the part that only real storage can
show — a field that never survives a write is the same bug as not storing it, and
it is the failure the whole decision is meant to end.
"""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply, Visibility

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal


def test_both_supplies_round_trip_and_only_cloud_is_reclaimable(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    async def _run() -> None:
        service = DeviceService(SqlDeviceRepository(db_session))

        # 入口决定待遇 on BOTH axes (#282 决定 2 / #358): a MicroCloud VM is
        # platform-provisioned (cloud) AND whole-machine (host — a fresh disposable
        # box is its own empty room); a human's enrolled desktop is self_hosted AND
        # boxed by default (isolated) until they opt into whole-machine.
        cloud = await service.approve(
            await service.start("microcloud-box"),
            owner_user_id=1,
            supply=Supply.cloud,
            visibility=Visibility.host,
        )
        mine = await service.approve(
            await service.start("my-desktop"),
            owner_user_id=1,
            supply=Supply.self_hosted,
            visibility=Visibility.isolated,
        )
        await db_session.flush()
        db_session.expunge_all()  # force a genuine read, not the identity map

        stored_cloud = await service.get_device(cloud.device_id)
        stored_mine = await service.get_device(mine.device_id)
        assert stored_cloud is not None and stored_mine is not None
        assert stored_cloud.supply is Supply.cloud
        assert stored_mine.supply is Supply.self_hosted
        # Both visibility values survive the round-trip through Postgres.
        assert stored_cloud.visibility is Visibility.host
        assert stored_mine.visibility is Visibility.isolated

        # The invariant, against real storage: the platform disposes of what it
        # opened and refuses what it did not.
        await service.delete_platform_provisioned(cloud.device_id, actor_user_id=1)
        assert await service.get_device(cloud.device_id) is None

        with pytest.raises(ForbiddenError):
            await service.delete_platform_provisioned(mine.device_id, actor_user_id=1)
        assert await service.get_device(mine.device_id) is not None

    _portal.call(_run)
