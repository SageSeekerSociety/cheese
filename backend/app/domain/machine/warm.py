"""Prepare unused machines and hand each to one room, with durable claim intent."""

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import settings
from app.core.db import SessionFactory
from app.core.errors import ValidationError
from app.domain.agent.device_hub import device_hub
from app.domain.device.models import DeviceRow
from app.domain.device.supply import Supply, Visibility
from app.domain.device.wiring import sql_device_service
from app.domain.identity.services import IdentityService
from app.domain.machine import enrollment
from app.domain.machine.microcloud import MicroCloudClient, MicroCloudError
from app.domain.machine.models import (
    AiStatus,
    MachineStatus,
    ProjectMachine,
    WarmMachine,
)
from app.domain.machine.repositories import ProjectMachineRepository
from app.domain.project.services import ProjectService

logger = logging.getLogger("cheese.machine.warm")
POOL_LOCK = 728104913


async def sweep_warm_pool(sessions: SessionFactory) -> None:
    async with sessions() as session:
        service = WarmPoolService(session)
        if service.client.configured:
            await service.sweep()


class WarmPoolService:
    def __init__(self, session: AsyncSession, client: MicroCloudClient | None = None):
        self.session = session
        self.client = client or MicroCloudClient()
        self.devices = sql_device_service(session)

    async def reserve(
        self,
        *,
        body: dict,
        project_id: uuid.UUID,
        topic_id: uuid.UUID,
        requested_by: str | None,
        owner_user_id: int,
    ) -> ProjectMachine | None:
        """Persist admission while the caller holds topic and team quota locks."""
        candidates = (
            await self.session.scalars(
                select(WarmMachine)
                .where(WarmMachine.state == "ready")
                .order_by(WarmMachine.created_at)
            )
        ).all()
        for warm in candidates:
            assert warm.machine_id is not None  # Ready rows have a provider identity.
            if not warm.device_id or not device_hub.is_online(warm.device_id):
                continue
            if any(
                warm.create_request.get(key) != body.get(key)
                for key in (
                    "offeringId",
                    "cores",
                    "memoryMb",
                    "diskGb",
                    "user",
                    "aiMode",
                )
            ):
                continue
            if warm.created_at < datetime.now(UTC) - timedelta(
                seconds=settings.microcloud_warm_max_age_seconds
            ):
                continue
            warm = await self.session.scalar(
                select(WarmMachine)
                .where(WarmMachine.id == warm.id, WarmMachine.state == "ready")
                .with_for_update(skip_locked=True)
            )
            if warm is None:
                continue
            assert warm.machine_id is not None
            machine = await ProjectMachineRepository(self.session).add(
                project_id=project_id,
                topic_id=topic_id,
                requested_by=requested_by,
                owner_user_id=owner_user_id,
                machine_id=warm.machine_id,
                customer_id=body["customerId"],
                account_id=body["accountId"],
                offering_id=body["offeringId"],
                hostname=warm.create_request["hostname"],
                login_user=body["user"],
                cores=body["cores"],
                memory_mb=body["memoryMb"],
                disk_gb=body["diskGb"],
                status=MachineStatus.starting,
                ip=warm.ip,
                ai_mode=body.get("aiMode", "none"),
                ai_status=AiStatus.provisioning,
            )
            warm.state = "reserved"
            warm.attempts = 0
            machine.warm_claim_pending = True
            warm.claimed_machine_id = machine.id
            await self.session.commit()
            await self.finish_claim(machine)
            return machine
        logger.info("warm pool miss topic=%s", topic_id)
        return None

    async def finish_claim(self, machine: ProjectMachine) -> bool:
        if machine.topic_id is None or not machine.warm_claim_pending:
            return False
        await ProjectMachineRepository(self.session).lock_topic(machine.topic_id)
        await self.session.refresh(machine)
        warm = await self.session.scalar(
            select(WarmMachine)
            .where(
                WarmMachine.claimed_machine_id == machine.id,
                WarmMachine.state.in_(["reserved", "claim_failed"]),
            )
            .with_for_update()
        )
        if warm is None:
            return False
        assert warm.machine_id is not None
        owner_user_id = machine.owner_user_id
        assert owner_user_id is not None  # A reservation is created for a room agent.
        if machine.released_at is not None or machine.status in {
            MachineStatus.deleted,
            MachineStatus.deleting,
        }:
            warm.state = "deleting"
            warm.attempts = 0
            await self.session.commit()
            return False
        if warm.state == "claim_failed":
            warm.state = "reserved"
            warm.attempts = 0
        started = time.monotonic()
        # The room lease already counts against quota. Keep it reserved on timeout, and
        # retry this exact recipient; allocating a second machine would leak the first.
        try:
            upstream = await self.client.claim_warm_machine(
                warm.machine_id,
                {
                    "claimKey": str(machine.topic_id),
                    "customerId": machine.customer_id,
                    "accountId": machine.account_id,
                    "newapiAccountId": machine.account_id,
                    "ccproxyAccountId": machine.account_id,
                },
            )
        except MicroCloudError as exc:
            warm.attempts += 1
            warm.error = f"claim HTTP {exc.status}" if exc.status else "claim timeout"
            if warm.attempts >= 5 or exc.status in {400, 401, 403, 404}:
                warm.state = "claim_failed"
                machine.status = MachineStatus.error
                machine.enroll_error = (
                    "云端资源分配失败，请重试；也可以释放机器后重新创建。"
                )
            await self.session.commit()
            logger.warning(
                "warm claim pending topic=%s status=%s", machine.topic_id, exc.status
            )
            return False
        device = await self.session.get(DeviceRow, warm.device_id)
        if device is None or device.supply != Supply.cloud:
            raise ValidationError("预热机器连接已失效，请稍后重试")
        device.owner_user_id = owner_user_id
        project = await ProjectService(self.session).get(machine.project_id)
        if project is None:
            raise ValidationError("项目不存在")
        await self.session.flush()
        if project.team_id is not None:
            await self.devices.assign_to_team(
                device.device_id, project.team_id, actor_user_id=owner_user_id
            )
        else:
            await self.devices.assign_to_project(
                device.device_id, project.id, actor_user_id=owner_user_id
            )
        machine.device_id = warm.device_id
        machine.warm_claim_pending = False
        machine.enroll_error = None
        machine.ccproxy_upstream = warm.ccproxy_upstream
        machine.enrolled_at = warm.enrolled_at
        machine.status = MachineStatus(upstream["status"])
        machine.ai_status = AiStatus(upstream["aiStatus"])
        machine.last_seen_at = datetime.now(UTC)
        warm.state = "claimed"
        warm.error = None
        await self.session.commit()
        logger.info(
            "warm claim complete topic=%s duration_ms=%d",
            machine.topic_id,
            (time.monotonic() - started) * 1000,
        )
        return True

    async def sweep(self) -> None:
        """A dedicated connection holds the worker lock across checkpoint commits."""
        engine = self.session.bind
        assert isinstance(engine, AsyncEngine)
        async with engine.connect() as connection:
            locked = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": POOL_LOCK}
            )
            if not locked:
                return
            try:
                await self._sweep_locked()
            finally:
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": POOL_LOCK}
                )

    async def _sweep_locked(self) -> None:
        abandoned = (
            await self.session.scalars(
                select(WarmMachine)
                .outerjoin(
                    ProjectMachine, WarmMachine.claimed_machine_id == ProjectMachine.id
                )
                .where(
                    WarmMachine.state.in_(["reserved", "claim_failed"]),
                    or_(ProjectMachine.id.is_(None), ProjectMachine.topic_id.is_(None)),
                )
                .with_for_update(of=WarmMachine)
            )
        ).all()
        for row in abandoned:
            row.state = "deleting"
            row.attempts = 0
        await self.session.commit()
        pending = (
            await self.session.scalars(
                select(ProjectMachine)
                .join(WarmMachine, WarmMachine.claimed_machine_id == ProjectMachine.id)
                .where(
                    or_(
                        WarmMachine.state == "reserved",
                        and_(
                            WarmMachine.state == "claim_failed",
                            ProjectMachine.released_at.is_not(None),
                        ),
                    )
                )
            )
        ).all()
        for machine in pending:
            await self.finish_claim(machine)
        rows = (
            await self.session.scalars(
                select(WarmMachine)
                .where(WarmMachine.state.in_(["preparing", "ready", "deleting"]))
                .order_by(WarmMachine.created_at)
            )
        ).all()
        for row in rows:
            # Reserve takes the same row lock; refresh after waiting for it.
            await self.session.refresh(row, with_for_update=True)
            if row.state not in {"preparing", "ready", "deleting"}:
                continue
            expired = row.created_at < datetime.now(UTC) - timedelta(
                seconds=settings.microcloud_warm_max_age_seconds
            )
            if (
                expired
                or row.attempts >= 5
                or row.state == "deleting"
                or not settings.microcloud_warm_pool_size
            ):
                if row.state != "deleting":
                    row.attempts = 0
                    row.state = "deleting"
                await self.session.commit()
                try:
                    await self._delete(row)
                except MicroCloudError as exc:
                    row.attempts += 1
                    row.error = f"delete HTTP {exc.status}"
                    if row.attempts >= 5:
                        row.state = "cleanup_failed"
                    await self.session.commit()
                    logger.error(
                        "warm cleanup failed id=%s attempt=%s", row.id, row.attempts
                    )
                continue
            try:
                await self._prepare(row)
            except (MicroCloudError, enrollment.EnrollmentError) as exc:
                row.attempts += 1
                # SSH failures may contain bootstrap output. Do not persist it.
                row.error = type(exc).__name__
                logger.warning(
                    "warm preparation failed id=%s attempt=%d type=%s",
                    row.id,
                    row.attempts,
                    type(exc).__name__,
                )
            await self.session.commit()
        active = (
            await self.session.scalars(
                select(WarmMachine).where(
                    WarmMachine.state.in_(
                        [
                            "preparing",
                            "ready",
                            "deleting",
                            "cleanup_failed",
                            "reserved",
                            "claim_failed",
                        ]
                    )
                )
            )
        ).all()
        if len(active) < settings.microcloud_warm_pool_size:
            await self._new()

    async def _new(self) -> None:
        from app.domain.machine.services import MachineService

        origin = settings.connector_public_base.rstrip("/")
        if not origin or "localhost" in origin or "127.0.0.1" in origin:
            raise ValidationError(
                "warm pool requires a reachable connector_public_base"
            )
        mode = settings.microcloud_ai_mode.strip().lower()
        if mode != "ccproxy":
            raise ValidationError("warm pool requires ccproxy AI mode")
        offering = await MachineService(self.session, self.client)._pick_offering()
        ref = "cheese-platform-warm-pool"
        customer = await self.client.find_customer(
            ref
        ) or await self.client.create_customer(ref)
        account = await self.client.find_account(customer["id"], "warm-pool")
        if account is None:
            account = await self.client.create_account(customer["id"], "warm-pool")
            if settings.microcloud_initial_funds > 0:
                await self.client.topup(
                    account["id"], settings.microcloud_initial_funds, remark=ref
                )
        key = uuid.uuid4()
        private, public = await enrollment.generate_keypair()
        body = {
            "customerId": customer["id"],
            "accountId": account["id"],
            "hostname": f"warm-{key.hex[:16]}",
            "warmPoolKey": str(key),
            "offeringId": int(offering["id"]),
            "user": settings.microcloud_login_user,
            "aiMode": mode,
            "sshPubkey": enrollment.combine_authorized_keys(
                public, settings.microcloud_operator_ssh_pubkey
            ),
        }
        for name, value in (
            ("cores", settings.microcloud_default_cores),
            ("memoryMb", settings.microcloud_default_memory_mb),
            ("diskGb", settings.microcloud_default_disk_gb),
        ):
            body[name] = max(
                int(offering[f"{name}Min"]), min(int(offering[f"{name}Max"]), value)
            )
        row = WarmMachine(id=key, create_request=body, bootstrap_key=private)
        self.session.add(row)
        await self.session.commit()
        logger.info(
            "warm preparation scheduled id=%s offering=%s", key, body["offeringId"]
        )

    async def _prepare(self, row: WarmMachine) -> None:
        if row.machine_id is None:
            machine = await self.client.create_machine(row.create_request)
            row.machine_id = int(machine["id"])
            await self.session.commit()
        else:
            machine = await self.client.get_machine(row.machine_id)
        if machine is None:
            row.state = "deleting"
            return
        if machine.get("status") == "error" or machine.get("aiStatus") == "error":
            row.state = "deleting"
            return
        if machine.get("status") != "running" or machine.get("aiStatus") not in {
            "ready",
            "disabled",
        }:
            return
        ip = machine.get("ip")
        row.ip = ip
        if not ip:
            return
        if not row.enrolled_at:
            if not row.device_id:
                owner = await IdentityService(self.session).ensure_agent_user(
                    handle="cheese-warm-pool"
                )
                code = await self.devices.start(row.create_request["hostname"])
                device = await self.devices.approve(
                    code,
                    owner_user_id=owner.id,
                    supply=Supply.cloud,
                    visibility=Visibility.host,
                    name=row.create_request["hostname"],
                )
                row.device_id = device.device_id
                await self.session.commit()
            device = await self.session.get(DeviceRow, row.device_id)
            assert device is not None
            assert row.bootstrap_key is not None
            script = enrollment.bootstrap_script(
                origin=settings.connector_public_base,
                token=device.token,
                device_id=device.device_id,
            )
            output = await enrollment.run_bootstrap(
                ip=ip,
                login_user=row.create_request["user"],
                private_key=row.bootstrap_key,
                script=script,
            )
            row.ccproxy_upstream = enrollment.parse_ccproxy_upstream(output)
            row.enrolled_at = datetime.now(UTC)
            row.bootstrap_key = None
        if row.device_id and device_hub.is_online(row.device_id):
            row.state = "ready"
            row.error = None

    async def _delete(self, row: WarmMachine) -> None:
        # Recover an uncertain creation's ID before deleting its billed resource.
        if row.machine_id is None:
            created = await self.client.create_machine(row.create_request)
            row.machine_id = int(created["id"])
            await self.session.commit()
        try:
            await self.client.delete_machine(row.machine_id)
        except MicroCloudError as exc:
            if exc.status != 404:
                raise
        if await self.client.get_machine(row.machine_id) is not None:
            # Count accepted deletion until the provider confirms the machine gone.
            await self.session.commit()
            return
        if row.device_id:
            device = await self.session.get(DeviceRow, row.device_id)
            if device is not None:
                await self.devices.delete_platform_provisioned(
                    row.device_id, actor_user_id=device.owner_user_id
                )
        row.state = "deleted"
        row.bootstrap_key = None
        row.ccproxy_upstream = None
        await self.session.commit()
