"""Project machine data access."""

import uuid
from datetime import datetime

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceRow, DeviceTopicRow
from app.domain.machine.models import (
    AI_TRANSITIONAL,
    GONE,
    MAX_ENROLL_ATTEMPTS,
    TRANSITIONAL,
    AiStatus,
    MachineStatus,
    ProjectMachine,
)


class ProjectMachineRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def add(
        self,
        *,
        project_id: uuid.UUID,
        topic_id: uuid.UUID | None = None,
        machine_id: int,
        customer_id: int,
        account_id: int,
        offering_id: int,
        hostname: str,
        login_user: str,
        cores: int,
        memory_mb: int,
        disk_gb: int,
        status: MachineStatus,
        ip: str | None,
        requested_by: str | None,
        ai_mode: str = "none",
        ai_status: AiStatus = AiStatus.unknown,
        owner_user_id: int | None = None,
        bootstrap_key: str | None = None,
    ) -> ProjectMachine:
        machine = ProjectMachine(
            project_id=project_id,
            topic_id=topic_id,
            machine_id=machine_id,
            customer_id=customer_id,
            account_id=account_id,
            offering_id=offering_id,
            hostname=hostname,
            login_user=login_user,
            cores=cores,
            memory_mb=memory_mb,
            disk_gb=disk_gb,
            status=status,
            ip=ip,
            requested_by=requested_by,
            ai_mode=ai_mode,
            ai_status=ai_status,
            owner_user_id=owner_user_id,
            bootstrap_key=bootstrap_key,
        )
        self._session.add(machine)
        await self._session.flush()
        await self._session.refresh(machine)
        return machine

    async def get(self, machine_row_id: uuid.UUID) -> ProjectMachine | None:
        return await self._session.get(ProjectMachine, machine_row_id)

    async def lock_provisioning(
        self, project_id: uuid.UUID, topic_id: uuid.UUID
    ) -> None:
        """Serialize paid creates for a project before calling MicroCloud.

        The partial unique index is the durable invariant for one active row per
        topic. This transaction lock closes the earlier external side-effect race:
        two requests must not both create a VM and only then discover the index.
        Project scope also makes the existing per-project quota concurrency-safe.
        """
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": f"cloud-project:{project_id}"},
        )
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": f"cloud-topic:{topic_id}"},
        )

    async def lock_topic(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": f"cloud-topic:{topic_id}"},
        )

    async def get_active_for_topic(self, topic_id: uuid.UUID) -> ProjectMachine | None:
        result = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.topic_id == topic_id,
                ProjectMachine.released_at.is_(None),
            )
        )
        return result.scalars().one_or_none()

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
        result = await self._session.execute(
            select(ProjectMachine)
            .where(ProjectMachine.project_id == project_id)
            .order_by(ProjectMachine.created_at)
        )
        return list(result.scalars())

    async def find_by_hostname(
        self, project_id: uuid.UUID, hostname: str
    ) -> ProjectMachine | None:
        result = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.project_id == project_id,
                ProjectMachine.hostname == hostname,
            )
        )
        return result.scalars().first()

    async def set_state(
        self,
        machine: ProjectMachine,
        *,
        status: MachineStatus,
        ip: str | None,
        ai_mode: str | None = None,
        ai_status: AiStatus | None = None,
        seen_at: datetime | None = None,
    ) -> ProjectMachine:
        machine.status = status
        if ai_mode is not None:
            machine.ai_mode = ai_mode
        if ai_status is not None:
            machine.ai_status = ai_status
        if seen_at is not None:
            machine.last_seen_at = seen_at
        # Never blank an IP we already learned: a transient read that omits it
        # would otherwise erase the only way back into a running machine.
        if ip:
            machine.ip = ip
        await self._session.flush()
        return machine

    async def touch_seen(
        self, machine: ProjectMachine, *, when: datetime
    ) -> ProjectMachine:
        """Record that MicroCloud was asked, without claiming to have learned
        anything. Used when the provider was unreachable and the machine's last
        known state is still the best answer we have."""
        machine.last_seen_at = when
        await self._session.flush()
        return machine

    async def delete(self, machine: ProjectMachine) -> None:
        await self._session.delete(machine)
        await self._session.flush()

    async def mark_released(
        self, machine: ProjectMachine, *, when: datetime
    ) -> ProjectMachine:
        machine.released_at = when
        await self._session.flush()
        return machine

    async def mark_enrolled(
        self,
        machine: ProjectMachine,
        *,
        device_id: str,
        when: datetime,
        ccproxy_upstream: str | None = None,
    ) -> ProjectMachine:
        machine.device_id = device_id
        machine.enrolled_at = when
        machine.enroll_error = None
        # Only ever set, never cleared: the bootstrap key is erased below, so a
        # re-run that came back empty could not recover it, and blanking a known
        # identity would silently drop the machine back to the shared one.
        if ccproxy_upstream:
            machine.ccproxy_upstream = ccproxy_upstream
        # The bootstrap key existed for this one setup; keeping it would leave a
        # standing way into the machine that nobody asked for.
        machine.bootstrap_key = None
        await self._session.flush()
        return machine

    async def mark_enroll_failed(
        self, machine: ProjectMachine, *, error: str
    ) -> ProjectMachine:
        machine.enroll_error = error[:1000]
        machine.enroll_attempts = (machine.enroll_attempts or 0) + 1
        await self._session.flush()
        return machine

    async def list_awaiting_enrollment(
        self,
        limit: int,
        *,
        desired_ai_mode: str = "",
        settle_cutoff: datetime | None = None,
    ) -> list[ProjectMachine]:
        """Machines that are up, have their agent access wired, and are not yet
        enrolled — and that we have not already given up on.

        Also waits for the machine's AI channel to reach the mode this
        deployment asked for, because enrollment is the ONE moment the platform
        is on the machine over ssh (`mark_enrolled` erases the bootstrap key)
        and what it reads there — the machine's ccproxy identity — only exists
        once MicroCloud has written the settings for that mode. Enrolling a
        machine still on the provisioning default records no identity, and there
        is no second chance: observed live 2026-08-14, machine 472 enrolled at
        `newapi/ready`, was switched to ccproxy a sweep later, and can never have
        its identity read again.

        `settle_cutoff` bounds that wait. A machine whose channel never reaches
        the desired mode must still become usable compute — it just falls back to
        the deployment-wide identity, which is a supported state. Waiting forever
        would turn a degraded machine into a dead one.
        """
        conditions = [
            ProjectMachine.device_id.is_(None),
            ProjectMachine.status == MachineStatus.running,
            ProjectMachine.ai_status == AiStatus.ready,
            ProjectMachine.bootstrap_key.is_not(None),
            ProjectMachine.ip.is_not(None),
            ProjectMachine.enroll_attempts < MAX_ENROLL_ATTEMPTS,
        ]
        if desired_ai_mode:
            settled = ProjectMachine.ai_mode == desired_ai_mode
            conditions.append(
                settled
                if settle_cutoff is None
                else or_(settled, ProjectMachine.created_at < settle_cutoff)
            )
        result = await self._session.execute(
            select(ProjectMachine)
            .where(*conditions)
            .order_by(ProjectMachine.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def list_unsettled(self, limit: int) -> list[ProjectMachine]:
        """Machines whose lifecycle can still change on its own.

        The sweep needs this because the ONLY place that refreshes a machine
        from MicroCloud is `list_for_project` — a read path, so a machine that
        finishes provisioning while nobody has the project open keeps whatever
        state it had at the last read. Enrolment waits on `ai_status`, so a row
        frozen at `provisioning` is a machine that never becomes compute:
        observed live 2026-08-14, machine 473 sat unenrolled for 13 minutes
        while MicroCloud had reported it `ready` the whole time.
        """
        result = await self._session.execute(
            select(ProjectMachine)
            .where(
                ProjectMachine.status.not_in(GONE),
                or_(
                    ProjectMachine.status.in_(TRANSITIONAL),
                    ProjectMachine.status == MachineStatus.unknown,
                    ProjectMachine.ai_status.in_(AI_TRANSITIONAL),
                    ProjectMachine.ai_status == AiStatus.unknown,
                ),
            )
            .order_by(ProjectMachine.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def ccproxy_upstream_for_topic(self, topic_id: uuid.UUID) -> str | None:
        """The ccproxy identity of the machine this topic's turns run on.

        One join rather than two round trips, because the metering proxy asks
        this on the admission path — the hop every turn already waits on. The
        chain is topic → pinned device → machine: a topic's work tree and its
        resumable claude session live on ONE machine, and that pin is write-once
        (``bind_topic_device``), so the answer is stable for the topic's life.

        Two sources, one meaning. A MicroCloud machine's identity is captured at
        enrollment into `ProjectMachine`; a self-hosted device has no enrollment,
        so its identity lives on `DeviceRow` (set by whoever administers the
        device — the dev box first). Checked in that order; they cannot disagree,
        because a device is only ever one of the two kinds.

        None whenever every link is missing — an unpinned topic, a device that
        brings no identity, a machine enrolled before the identity was recorded.
        Every one of those means "use the deployment-wide identity", which is
        the behaviour those turns have today.
        """
        from_machine = await self._session.scalar(
            select(ProjectMachine.ccproxy_upstream)
            .join(DeviceTopicRow, DeviceTopicRow.device_id == ProjectMachine.device_id)
            .where(
                DeviceTopicRow.topic_id == topic_id,
                ProjectMachine.ccproxy_upstream.is_not(None),
            )
        )
        if from_machine:
            return from_machine
        return await self._session.scalar(
            select(DeviceRow.ccproxy_upstream)
            .join(DeviceTopicRow, DeviceTopicRow.device_id == DeviceRow.device_id)
            .where(
                DeviceTopicRow.topic_id == topic_id,
                DeviceRow.ccproxy_upstream.is_not(None),
            )
        )

    async def list_ai_mode_mismatch(
        self, desired: str, limit: int
    ) -> list[ProjectMachine]:
        """Machines whose built-in AI channel settled on the wrong mode.

        Only settled machines (running + ai ready) are candidates — a machine
        still provisioning will be judged when it lands, and fighting a
        transitional state would race MicroCloud's own wiring."""
        result = await self._session.execute(
            select(ProjectMachine)
            .where(
                ProjectMachine.status == MachineStatus.running,
                ProjectMachine.ai_status == AiStatus.ready,
                ProjectMachine.ai_mode != desired,
            )
            .order_by(ProjectMachine.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    # `is_provisioned_device` lived here until #282 决定 2. It answered "was this
    # device provisioned by the platform" by asking whether any row in THIS table
    # pointed at it — the reverse lookup #282 is about. The fact now lives on
    # `device.supply`, where it is read; `check-repo-rules.sh` fails the build if
    # a new caller reintroduces the join.
