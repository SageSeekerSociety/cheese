"""Cloud host pool data access."""

import uuid
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    MAX_ENROLL_ATTEMPTS,
    TRANSITIONAL,
    AiStatus,
    CloudHost,
    CloudHostHome,
    MachineStatus,
)


class Load(NamedTuple):
    """What one host carries: the sandboxes that run (or may, being placed),
    and every home on its disk, the asleep ones included."""

    running: int
    stored: int


class CloudHostRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def lock_pool(self) -> None:
        """Placement and release decide on the same counts: one at a time, for
        the rest of the transaction. Never held across a provider call."""
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": "cloud-host-pool"},
        )

    async def add(self, **fields) -> CloudHost:
        host = CloudHost(**fields)
        self._session.add(host)
        await self._session.flush()
        await self._session.refresh(host)
        return host

    async def get(self, host_id: uuid.UUID) -> CloudHost | None:
        return await self._session.get(CloudHost, host_id)

    async def by_device(self, device_id: str) -> CloudHost | None:
        return await self._session.scalar(
            select(CloudHost).where(CloudHost.device_id == device_id)
        )

    async def live(self) -> list[CloudHost]:
        """Every host still in the pool, oldest first."""
        return list(
            await self._session.scalars(
                select(CloudHost)
                .where(CloudHost.released_at.is_(None))
                .order_by(CloudHost.created_at, CloudHost.id)
            )
        )

    async def occupancy(self) -> dict[uuid.UUID, Load]:
        """What each live host carries (hosts with no home are absent)."""
        rows = await self._session.execute(
            select(
                CloudHostHome.host_id,
                func.count().filter(CloudHostHome.stopped_at.is_(None)),
                func.count(),
            )
            .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
            .where(CloudHost.released_at.is_(None))
            .group_by(CloudHostHome.host_id)
        )
        return {
            host_id: Load(int(running), int(stored))
            for host_id, running, stored in rows.all()
        }

    async def failures_since(self, since: datetime) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(CloudHost)
                .where(CloudHost.failed_at >= since)
            )
            or 0
        )

    # --- homes ---------------------------------------------------------------

    async def put_home(
        self, host_id: uuid.UUID, home: dict | CloudHostHome
    ) -> CloudHostHome:
        """Place a home on a host: a new one from its fields, or an archived
        one, which keeps its row (and its archive, to restore from)."""
        if isinstance(home, CloudHostHome):
            home.host_id = host_id
        else:
            home = CloudHostHome(host_id=host_id, **home)
            self._session.add(home)
        await self._session.flush()
        return home

    async def unplace_archived(self, host_id: uuid.UUID) -> None:
        """Homes placed on this host whose archive was never restored there go
        back to being archived: the host is going, and their work is only in
        the bucket."""
        await self._session.execute(
            update(CloudHostHome)
            .where(
                CloudHostHome.host_id == host_id,
                CloudHostHome.archive_key.is_not(None),
            )
            .values(host_id=None)
        )

    async def lock_home(self, home_id: uuid.UUID) -> CloudHostHome | None:
        return await self._session.scalar(
            select(CloudHostHome)
            .where(CloudHostHome.id == home_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def current_home(self, session_id: uuid.UUID) -> CloudHostHome | None:
        """The home of the session's current placement (not one it left), as
        the database has it now: a sweep may have put it to sleep or archived
        it since this session last read it."""
        return await self._session.scalar(
            select(CloudHostHome)
            .where(
                CloudHostHome.session_id == session_id,
                CloudHostHome.left_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )

    async def homes_on(self, host_id: uuid.UUID) -> list[CloudHostHome]:
        return list(
            await self._session.scalars(
                select(CloudHostHome).where(CloudHostHome.host_id == host_id)
            )
        )

    async def room_homes(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> list[tuple[CloudHostHome, CloudHost]]:
        rows = await self._session.execute(
            select(CloudHostHome, CloudHost)
            .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
            .where(
                CloudHostHome.topic_id == topic_id,
                CloudHostHome.room_resource_id == room_resource_id,
                CloudHostHome.left_at.is_(None),
            )
        )
        return [(home, host) for home, host in rows.all()]

    async def waiting_homes(self) -> list[tuple[CloudHostHome, CloudHost]]:
        """Homes whose room was told their sandbox is being prepared."""
        rows = await self._session.execute(
            select(CloudHostHome, CloudHost)
            .join(CloudHost, CloudHost.id == CloudHostHome.host_id)
            .where(CloudHostHome.waiting_since.is_not(None))
        )
        return [(home, host) for home, host in rows.all()]

    async def delete_home(self, home: CloudHostHome) -> None:
        await self._session.delete(home)
        await self._session.flush()

    async def delete_device_homes(self, device_id: str, resource_id: str) -> None:
        """The homes in the directory a room's cleanup removed from a host."""
        hosts = select(CloudHost.id).where(CloudHost.device_id == device_id)
        await self._session.execute(
            delete(CloudHostHome).where(
                CloudHostHome.host_id.in_(hosts),
                CloudHostHome.resource_id == resource_id,
            )
        )

    async def room_archives(
        self, topic_id: uuid.UUID, room_resource_id: str | None = None
    ) -> list[CloudHostHome]:
        """The room's homes that have an archive in the bucket, of one
        generation or of every one."""
        query = select(CloudHostHome).where(
            CloudHostHome.topic_id == topic_id,
            CloudHostHome.archive_key.is_not(None),
        )
        if room_resource_id is not None:
            query = query.where(CloudHostHome.room_resource_id == room_resource_id)
        return list(await self._session.scalars(query))

    async def delete_room_homes(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> None:
        await self._session.execute(
            delete(CloudHostHome).where(
                CloudHostHome.topic_id == topic_id,
                CloudHostHome.room_resource_id == room_resource_id,
            )
        )

    # --- the sweep -----------------------------------------------------------

    async def set_state(
        self,
        host: CloudHost,
        *,
        status: MachineStatus,
        ip: str | None,
        ai_mode: str | None = None,
        ai_status: AiStatus | None = None,
        seen_at: datetime | None = None,
        machine_id: int | None = None,
    ) -> CloudHost:
        if machine_id is not None:
            host.machine_id = machine_id
        host.status = status
        if ai_mode is not None:
            host.ai_mode = ai_mode
        if ai_status is not None:
            host.ai_status = ai_status
        if seen_at is not None:
            host.last_seen_at = seen_at
        # Never blank an IP we already learned: a transient read that omits it
        # would otherwise erase the only way back into a running machine.
        if ip:
            host.ip = ip
        await self._session.flush()
        return host

    async def touch_seen(self, host: CloudHost, *, when: datetime) -> CloudHost:
        """Record that MicroCloud was asked, without claiming to have learned
        anything: the provider was unreachable and the last known state is
        still the best answer there is."""
        host.last_seen_at = when
        await self._session.flush()
        return host

    async def delete(self, host: CloudHost) -> None:
        await self.unplace_archived(host.id)
        await self._session.delete(host)
        await self._session.flush()

    async def list_reservations_older_than(self, cutoff: datetime) -> list[CloudHost]:
        """Hosts still waiting for a provider id past the point a create can take."""
        return list(
            await self._session.scalars(
                select(CloudHost).where(
                    CloudHost.machine_id.is_(None),
                    CloudHost.warm_claim_pending.is_(False),
                    CloudHost.released_at.is_(None),
                    CloudHost.created_at < cutoff,
                )
            )
        )

    async def list_released_undeleted(self) -> list[CloudHost]:
        """Hosts taken out of the pool whose provider delete has not landed."""
        return list(
            await self._session.scalars(
                select(CloudHost).where(
                    CloudHost.released_at.is_not(None),
                    CloudHost.status.not_in(
                        [MachineStatus.deleting, MachineStatus.deleted]
                    ),
                )
            )
        )

    async def mark_enrolled(
        self, host: CloudHost, *, device_id: str, when: datetime
    ) -> CloudHost:
        host.device_id = device_id
        host.enrolled_at = when
        host.enroll_error = None
        # The bootstrap key existed for this one setup; keeping it would leave a
        # standing way into the machine that nobody asked for.
        host.bootstrap_key = None
        await self._session.flush()
        return host

    async def mark_enroll_failed(self, host: CloudHost, *, error: str) -> CloudHost:
        host.enroll_error = error[:1000]
        host.enroll_attempts = (host.enroll_attempts or 0) + 1
        await self._session.flush()
        return host

    async def list_awaiting_enrollment(self, limit: int) -> list[CloudHost]:
        """Hosts that are up and not yet enrolled — and not given up on."""
        return list(
            await self._session.scalars(
                select(CloudHost)
                .where(
                    CloudHost.device_id.is_(None),
                    CloudHost.released_at.is_(None),
                    CloudHost.status == MachineStatus.running,
                    CloudHost.bootstrap_key.is_not(None),
                    CloudHost.ip.is_not(None),
                    CloudHost.enroll_attempts < MAX_ENROLL_ATTEMPTS,
                )
                .order_by(CloudHost.created_at)
                .limit(limit)
            )
        )

    async def list_due(self, limit: int, *, seen_before: datetime) -> list[CloudHost]:
        """Hosts the sweep must bring in line with MicroCloud.

        Those whose lifecycle can still change; settled ones last checked
        before `seen_before`, since MicroCloud may have destroyed them; and the
        gone ones the sweep only has to let go.
        """
        changing = or_(
            CloudHost.status.in_(TRANSITIONAL),
            CloudHost.status == MachineStatus.unknown,
            CloudHost.ai_status.in_(AI_TRANSITIONAL),
            CloudHost.ai_status == AiStatus.unknown,
        )
        live = CloudHost.status.not_in(GONE)
        moving = await self._session.scalars(
            select(CloudHost)
            .where(live, changing, CloudHost.released_at.is_(None))
            .order_by(CloudHost.created_at)
            .limit(limit)
        )
        # Oldest check first, so every settled host is reached in turn. A
        # released one is still checked until MicroCloud confirms it gone.
        stale = await self._session.scalars(
            select(CloudHost)
            .where(
                live,
                ~changing | CloudHost.released_at.is_not(None),
                CloudHost.machine_id.is_not(None),
                CloudHost.warm_claim_pending.is_(False),
                or_(
                    CloudHost.last_seen_at.is_(None),
                    CloudHost.last_seen_at < seen_before,
                ),
            )
            .order_by(CloudHost.last_seen_at.asc().nulls_first())
            .limit(limit)
        )
        gone = await self._session.scalars(
            select(CloudHost).where(CloudHost.status.in_(GONE))
        )
        seen: dict[uuid.UUID, CloudHost] = {}
        for host in [*moving, *stale, *gone]:
            seen.setdefault(host.id, host)
        return list(seen.values())
