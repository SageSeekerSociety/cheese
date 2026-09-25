"""Project machine data access."""

import uuid
from datetime import datetime

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.device.models import DeviceTopicRow
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
        session_id: uuid.UUID | None = None,
        machine_id: int | None,
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
            session_id=session_id,
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

    async def has_active_turn(self, machine: ProjectMachine) -> bool:
        from app.domain.agent.models import AgentTurn
        from app.domain.agent_session.models import AgentSession

        topics = select(DeviceTopicRow.topic_id).where(
            DeviceTopicRow.device_id == machine.device_id
        )
        sessions = select(AgentSession.topic_id).where(
            or_(
                AgentSession.runtime_location["device_id"].as_string()
                == machine.device_id,
                AgentSession.work_lease["device_id"].as_string() == machine.device_id,
            )
        )
        affected = [AgentTurn.topic_id == machine.topic_id] if machine.topic_id else []
        if machine.device_id:
            affected.extend(
                [AgentTurn.topic_id.in_(topics), AgentTurn.topic_id.in_(sessions)]
            )
        if not affected:
            return False
        return (
            await self._session.scalar(
                select(AgentTurn.id)
                .where(AgentTurn.stopped_at.is_(None), or_(*affected))
                .limit(1)
            )
        ) is not None

    async def lock_team_quota(self, team_id: int) -> None:
        """Hold the team's last slot through the provider call and DB commit."""
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": f"cloud-team:{team_id}"},
        )

    async def lock_topic(self, topic_id: uuid.UUID) -> None:
        await self._session.execute(
            text(
                "SELECT pg_advisory_xact_lock(hashtextextended(CAST(:key AS text), 0))"
            ),
            {"key": f"cloud-topic:{topic_id}"},
        )

    async def get_active_for_topic(self, topic_id: uuid.UUID) -> ProjectMachine | None:
        """The legacy room allocation, never an arbitrary agent's allocation."""
        result = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.topic_id == topic_id,
                ProjectMachine.session_id.is_(None),
                ProjectMachine.released_at.is_(None),
            )
        )
        return result.scalars().one_or_none()

    async def get_active_for_session(
        self, session_id: uuid.UUID
    ) -> ProjectMachine | None:
        return (
            await self._session.scalars(
                select(ProjectMachine).where(
                    ProjectMachine.session_id == session_id,
                    ProjectMachine.released_at.is_(None),
                    ProjectMachine.superseded_at.is_(None),
                )
            )
        ).one_or_none()

    async def list_active_for_topic(self, topic_id: uuid.UUID) -> list[ProjectMachine]:
        """All unreleased resources, including superseded VMs awaiting cleanup."""
        return list(
            await self._session.scalars(
                select(ProjectMachine)
                .where(
                    ProjectMachine.topic_id == topic_id,
                    ProjectMachine.released_at.is_(None),
                )
                .order_by(ProjectMachine.created_at, ProjectMachine.id)
            )
        )

    async def list_ready_topic_devices(
        self, device_id: str | None = None
    ) -> list[tuple[uuid.UUID, str]]:
        """Cloud leases whose two provider lifecycles and enrolment have settled —
        every one, or only the lease on ``device_id`` (the machine that just
        connected)."""
        conditions = [
            ProjectMachine.topic_id.is_not(None),
            # Session allocations are polled by their own lazy tool admission;
            # the legacy ready callback would bind this device to the whole room.
            ProjectMachine.session_id.is_(None),
            ProjectMachine.released_at.is_(None),
            ProjectMachine.status == MachineStatus.running,
            ProjectMachine.ai_status.in_((AiStatus.ready, AiStatus.disabled)),
            ProjectMachine.device_id.is_not(None),
        ]
        if device_id is not None:
            conditions.append(ProjectMachine.device_id == device_id)
        rows = await self._session.execute(
            select(ProjectMachine.topic_id, ProjectMachine.device_id).where(*conditions)
        )
        return [(topic_id, device_id) for topic_id, device_id in rows.all()]

    async def list_failed_topic_leases(self) -> list[ProjectMachine]:
        """Active topic leases whose machine or AI channel MicroCloud reports as
        failed — the ones no sweep will ever hand to `list_ready_topic_devices`,
        so somebody has to tell the room."""
        rows = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.topic_id.is_not(None),
                ProjectMachine.session_id.is_(None),
                ProjectMachine.released_at.is_(None),
                or_(
                    ProjectMachine.status == MachineStatus.error,
                    ProjectMachine.ai_status == AiStatus.error,
                ),
            )
        )
        return list(rows.scalars())

    async def list_for_project(self, project_id: uuid.UUID) -> list[ProjectMachine]:
        result = await self._session.execute(
            select(ProjectMachine)
            .where(ProjectMachine.project_id == project_id)
            .order_by(ProjectMachine.created_at)
        )
        return list(result.scalars())

    async def list_for_team(self, team_id: int) -> list[ProjectMachine]:
        from app.domain.project.services import ProjectService

        # Personal teams also own their pre-team projects, as on the project list.
        projects = await ProjectService(self._session).list_for_team(team_id)
        result = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.project_id.in_([p.id for p in projects])
            )
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
        machine_id: int | None = None,
    ) -> ProjectMachine:
        if machine_id is not None:
            machine.machine_id = machine_id
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

    async def list_reservations_older_than(
        self, cutoff: datetime
    ) -> list[ProjectMachine]:
        """Rows still waiting for a provider id past the point a create can take."""
        result = await self._session.execute(
            select(ProjectMachine).where(
                ProjectMachine.machine_id.is_(None),
                ProjectMachine.warm_claim_pending.is_(False),
                ProjectMachine.released_at.is_(None),
                ProjectMachine.created_at < cutoff,
            )
        )
        return list(result.scalars())

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
    ) -> ProjectMachine:
        machine.device_id = device_id
        machine.enrolled_at = when
        machine.enroll_error = None
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

    async def list_awaiting_enrollment(self, limit: int) -> list[ProjectMachine]:
        """Machines that are up and not yet enrolled — and that we have not
        already given up on."""
        result = await self._session.execute(
            select(ProjectMachine)
            .where(
                ProjectMachine.device_id.is_(None),
                ProjectMachine.status == MachineStatus.running,
                ProjectMachine.bootstrap_key.is_not(None),
                ProjectMachine.ip.is_not(None),
                ProjectMachine.enroll_attempts < MAX_ENROLL_ATTEMPTS,
            )
            .order_by(ProjectMachine.created_at)
            .limit(limit)
        )
        return list(result.scalars())

    async def list_unsettled(self, limit: int) -> list[ProjectMachine]:
        """Machines whose lifecycle can still change on its own.

        The sweep needs this because the ONLY place that refreshes a machine
        from MicroCloud is `list_for_project` — a read path, so a machine that
        finishes provisioning while nobody has the project open keeps whatever
        state it had at the last read. A room's lease waits on both `status`
        and `ai_status`, so a row frozen at a transitional value is a machine
        that never becomes compute: observed live 2026-08-14, machine 473 sat
        unused for 13 minutes while MicroCloud had reported it settled the
        whole time.
        """
        result = await self._session.execute(
            select(ProjectMachine)
            .where(
                ProjectMachine.status.not_in((*GONE, MachineStatus.suspended)),
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
        moving = list(result.scalars())
        suspended = await self._session.scalars(
            select(ProjectMachine).where(
                ProjectMachine.status == MachineStatus.suspended,
                ProjectMachine.released_at.is_(None),
                ProjectMachine.topic_id.is_not(None),
            )
        )
        # Dormant machines need no provider poll and must not consume the poll budget.
        return moving + list(suspended)

    # `is_provisioned_device` lived here until #282 决定 2. It answered "was this
    # device provisioned by the platform" by asking whether any row in THIS table
    # pointed at it — the reverse lookup #282 is about. The fact now lives on
    # `device.supply`, where it is read; `check-repo-rules.sh` fails the build if
    # a new caller reintroduces the join.
