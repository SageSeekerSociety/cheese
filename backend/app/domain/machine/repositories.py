"""Cloud host pool data access."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    MAX_ENROLL_ATTEMPTS,
    PROVIDER_ERROR_WINDOW,
    TRANSITIONAL,
    AiStatus,
    CloudHost,
    HostProjectStore,
    MachineStatus,
    SandboxHome,
)


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

    async def occupancy(self) -> dict[uuid.UUID, int]:
        """How many homes each live host carries (hosts with none are
        absent): each is a sandbox, or one being placed or destroyed."""
        rows = await self._session.execute(
            select(SandboxHome.host_id, func.count())
            .join(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(CloudHost.released_at.is_(None))
            .group_by(SandboxHome.host_id)
        )
        return {host_id: int(homes) for host_id, homes in rows.all()}

    async def failures_since(self, since: datetime) -> tuple[int, datetime | None]:
        """How many hosts the provider failed since ``since``, and the last."""
        count, last = (
            await self._session.execute(
                select(func.count(), func.max(CloudHost.failed_at)).where(
                    CloudHost.failed_at >= since
                )
            )
        ).one()
        return int(count), last

    # --- homes ---------------------------------------------------------------

    async def put_home(self, host_id: uuid.UUID, home: dict) -> SandboxHome:
        """Place a new home on a host, from its fields. The host now keeps its
        project's store too, whatever happens to this home later."""
        row = SandboxHome(host_id=host_id, **home)
        self._session.add(row)
        project_key = home.get("project_id") or home.get("topic_id")
        if project_key is not None:
            now = datetime.now(UTC)
            await self._session.execute(
                insert(HostProjectStore)
                .values(
                    host_id=host_id,
                    project_key=project_key,
                    last_placed_at=now,
                )
                .on_conflict_do_update(
                    index_elements=["host_id", "project_key"],
                    set_={"last_placed_at": now},
                )
            )
        await self._session.flush()
        return row

    async def stores(self, project_key: uuid.UUID) -> set[uuid.UUID]:
        """The hosts that have placed a sandbox of this project (or of this
        conversation, for one in no project), and so hold its store."""
        return set(
            await self._session.scalars(
                select(HostProjectStore.host_id).where(
                    HostProjectStore.project_key == project_key
                )
            )
        )

    async def put_device_home(self, device_id: str, home: dict) -> SandboxHome:
        """Place a new home on a person's computer, from its fields."""
        row = SandboxHome(device_id=device_id, **home)
        self._session.add(row)
        await self._session.flush()
        return row

    async def lock_home(self, home_id: uuid.UUID) -> SandboxHome | None:
        return await self._session.scalar(
            select(SandboxHome)
            .where(SandboxHome.id == home_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )

    async def lock_homes(self, home_ids: list[uuid.UUID]) -> list[SandboxHome]:
        """Those of the homes nobody else holds, locked in one statement.

        One another transaction holds is skipped, not waited on: a tool call
        that locked its own home and then deletes the rest of its host's
        (``HostPool.place`` giving up on a host) would otherwise wait on a
        home this batch holds while the batch waits on its home."""
        return list(
            await self._session.scalars(
                select(SandboxHome)
                .where(SandboxHome.id.in_(home_ids))
                .with_for_update(skip_locked=True)
                .execution_options(populate_existing=True)
            )
        )

    async def current_home(self, session_id: uuid.UUID) -> SandboxHome | None:
        """The home of the session's placement, as the database has it now:
        a sweep may have started destroying it since this session last read
        it."""
        return await self._session.scalar(
            select(SandboxHome)
            .where(SandboxHome.session_id == session_id)
            .execution_options(populate_existing=True)
        )

    async def homes_on(self, host_id: uuid.UUID) -> list[SandboxHome]:
        return list(
            await self._session.scalars(
                select(SandboxHome).where(SandboxHome.host_id == host_id)
            )
        )

    async def room_homes(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> list[tuple[SandboxHome, CloudHost]]:
        rows = await self._session.execute(
            select(SandboxHome, CloudHost)
            .join(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                SandboxHome.topic_id == topic_id,
                SandboxHome.room_resource_id == room_resource_id,
                # A home its session left is the idle sweep's, not the room's.
                SandboxHome.session_id.is_not(None),
            )
        )
        return [(home, host) for home, host in rows.all()]

    async def waiting_homes(self) -> list[tuple[SandboxHome, CloudHost]]:
        """Homes whose room was told their sandbox is being prepared, and that
        wait for their host to come up."""
        rows = await self._session.execute(
            select(SandboxHome, CloudHost)
            .join(CloudHost, CloudHost.id == SandboxHome.host_id)
            .where(
                SandboxHome.waiting_since.is_not(None),
                SandboxHome.stopped_at.is_(None),
            )
        )
        return [(home, host) for home, host in rows.all()]

    async def delete_home(self, home: SandboxHome) -> None:
        await self._session.delete(home)
        await self._session.flush()

    async def delete_homes_on(self, host_id: uuid.UUID) -> None:
        """Every home still recorded on this host, gone in one statement."""
        await self._session.execute(
            delete(SandboxHome).where(SandboxHome.host_id == host_id)
        )

    async def delete_device_homes(self, device_id: str, resource_id: str) -> None:
        """The homes in the directory a room's cleanup removed from a machine."""
        hosts = select(CloudHost.id).where(CloudHost.device_id == device_id)
        await self._session.execute(
            delete(SandboxHome).where(
                or_(
                    SandboxHome.host_id.in_(hosts),
                    SandboxHome.device_id == device_id,
                ),
                SandboxHome.resource_id == resource_id,
            )
        )

    async def delete_room_homes(
        self, topic_id: uuid.UUID, room_resource_id: str
    ) -> None:
        await self._session.execute(
            delete(SandboxHome).where(
                SandboxHome.topic_id == topic_id,
                SandboxHome.room_resource_id == room_resource_id,
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
        self,
        host: CloudHost,
        *,
        device_id: str,
        when: datetime,
        sandbox_disk_gb: int | None,
    ) -> CloudHost:
        host.device_id = device_id
        host.enrolled_at = when
        host.sandbox_disk_gb = sandbox_disk_gb
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
        # A host the provider failed stays until it is out of the window that
        # counts failures (`HostPool._provider_failing`).
        recently_failed = (
            CloudHost.failed_at >= datetime.now(UTC) - PROVIDER_ERROR_WINDOW
        )
        gone = await self._session.scalars(
            select(CloudHost).where(
                CloudHost.status.in_(GONE),
                or_(CloudHost.failed_at.is_(None), ~recently_failed),
            )
        )
        seen: dict[uuid.UUID, CloudHost] = {}
        for host in [*moving, *stale, *gone]:
            seen.setdefault(host.id, host)
        return list(seen.values())
