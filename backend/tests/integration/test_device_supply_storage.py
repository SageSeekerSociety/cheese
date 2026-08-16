"""供给形式落到存储 (#282 决定 2): the two new `device` columns survive a real
round-trip through Postgres, and the platform's reclaim door reads the stored
value rather than any join.

The unit tests cover the rule; this covers the part that only real storage can
show — a field that never survives a write is the same bug as not storing it, and
it is the failure the whole decision is meant to end.
"""

import importlib.util
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from app.domain.device.models import DeviceRow, DeviceTopicRow
from app.domain.device.service import DeviceService
from app.domain.device.sql_repository import SqlDeviceRepository
from app.domain.device.supply import Supply, Visibility
from app.domain.user.models import User

if TYPE_CHECKING:
    from anyio.from_thread import BlockingPortal

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d8f4a1c2e693_hosted_device_subtype.py"
)


def _load_hosted_backfill():
    spec = importlib.util.spec_from_file_location("_hosted_subtype", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.backfill_hosted_subtype


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


def test_visibility_defaults_to_isolated_when_omitted(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    """A device written WITHOUT a visibility value lands on `isolated`, not `host`
    — the access-safe reading (#358 #364). Locks both inner defaults #361 missed:
    the ORM `default=` (an ORM insert that omits the field) and the DDL
    `server_default` (any raw INSERT that bypasses the ORM entirely). `host` is
    whole-machine access and 申请制; it must never be reached by omission, and this
    is the test that reddens if either inner default drifts back."""

    async def _run() -> None:
        owner = User(
            username="grace",
            email="grace@example.io",
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db_session.add(owner)
        await db_session.flush()

        # ORM path: the model's `default=` fills the omitted field before INSERT.
        db_session.add(
            DeviceRow(
                device_id="devdefault01",
                name="omit-via-orm",
                token="tok-devdefault01",
                owner_user_id=owner.id,
                created_at=datetime.now(UTC),
            )
        )
        await db_session.flush()
        db_session.expunge_all()  # force a real read, not the identity map
        reloaded = await db_session.get(DeviceRow, "devdefault01")
        assert reloaded is not None
        assert reloaded.visibility is Visibility.isolated
        # The supply axis is untouched: its safe reading is self_hosted.
        assert reloaded.supply is Supply.self_hosted

        # DDL path: a raw INSERT naming neither column relies purely on the DB
        # server_default set by the migration — the layer that catches an INSERT
        # that never goes through the repository or the ORM.
        await db_session.execute(
            text(
                "INSERT INTO device (device_id, name, token, owner_user_id, "
                "created_at) VALUES (:id, :name, :token, :owner, :ts)"
            ),
            {
                "id": "devdefault02",
                "name": "omit-via-raw-sql",
                "token": "tok-devdefault02",
                "owner": owner.id,
                "ts": datetime.now(UTC),
            },
        )
        row = (
            await db_session.execute(
                text("SELECT visibility, supply FROM device WHERE device_id = :id"),
                {"id": "devdefault02"},
            )
        ).one()
        assert row.visibility == "isolated"
        assert row.supply == "self_hosted"

    _portal.call(_run)


def test_ccproxy_upstream_survives_the_round_trip_to_the_domain_object(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    """The bug that shipped in #410/#411 and only surfaced on the live box: the
    column was added to DeviceRow, but `get_device` returns the Device DATACLASS,
    which lacked the field — so `device.ccproxy_upstream` silently read None, the
    provider never signalled the launcher, and the dev box looped on the swap
    path. A unit test could not catch it (the provider's lookup is monkeypatched
    everywhere); only a real read of a real row does. This is that read."""

    async def _run() -> None:
        service = DeviceService(SqlDeviceRepository(db_session))
        dev = await service.approve(
            await service.start("box-with-identity"),
            owner_user_id=1,
            supply=Supply.self_hosted,
            visibility=Visibility.host,
        )
        # Set the identity the way an administrator does — straight on the row,
        # there being no enrollment path for a self-hosted device.
        await db_session.execute(
            text("UPDATE device SET ccproxy_upstream = :up WHERE device_id = :d"),
            {"up": "m161:secret", "d": dev.device_id},
        )
        await db_session.flush()
        db_session.expunge_all()  # force a genuine read, not the identity map

        stored = await service.get_device(dev.device_id)
        assert stored is not None
        # The whole point: the value reaches the domain object the provider reads.
        assert stored.ccproxy_upstream == "m161:secret"

        # And a device with no identity reads as None, never a stray "".
        plain = await service.approve(
            await service.start("plain-laptop"),
            owner_user_id=1,
            supply=Supply.self_hosted,
            visibility=Visibility.isolated,
        )
        await db_session.flush()
        db_session.expunge_all()
        stored_plain = await service.get_device(plain.device_id)
        assert stored_plain is not None and stored_plain.ccproxy_upstream is None

    _portal.call(_run)


def test_hosted_subtype_migration_backfills_devices_and_topic_visibility(
    db_session: AsyncSession, _portal: "BlockingPortal"
):
    backfill = _load_hosted_backfill()
    hosted_topic, cloud_topic = uuid.uuid4(), uuid.uuid4()

    async def _run() -> None:
        for device_id, supply in (
            ("legacy-hosted", Supply.self_hosted),
            ("legacy-cloud", Supply.cloud),
        ):
            db_session.add(
                DeviceRow(
                    device_id=device_id,
                    name=device_id,
                    token=f"tok-{device_id}",
                    owner_user_id=1,
                    created_at=datetime.now(UTC),
                    supply=supply,
                    visibility=Visibility.host,
                )
            )
        await db_session.flush()
        db_session.add_all(
            [
                DeviceTopicRow(topic_id=hosted_topic, device_id="legacy-hosted"),
                DeviceTopicRow(topic_id=cloud_topic, device_id="legacy-cloud"),
            ]
        )
        await db_session.flush()
        report = await db_session.run_sync(
            lambda session: backfill(session.connection())
        )
        hosted_ids = set(
            (
                await db_session.execute(text("SELECT device_id FROM hosted_device"))
            ).scalars()
        )
        rows = (
            await db_session.execute(
                text("SELECT topic_id, visibility FROM device_topic")
            )
        ).all()

        # Only self_hosted devices get a subtype row — that is the split. But BOTH
        # bindings get their device's visibility copied onto them.
        assert report == {"hosted_devices": 1, "topic_bindings": 2}
        assert hosted_ids == {"legacy-hosted"}
        visibility = {row.topic_id: row.visibility for row in rows}
        assert visibility[hosted_topic] == "host"
        # The cloud binding too, and this one is load-bearing: a backfill that
        # joined through `hosted_device` would leave every cloud topic on the
        # column's `isolated` default, which is the one value both
        # `resolve_pinned_device` and `host_swap` refuse — stranding every existing
        # cloud topic the moment this migration ran.
        assert visibility[cloud_topic] == "host"

    _portal.call(_run)
