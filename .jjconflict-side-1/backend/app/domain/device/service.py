"""``DeviceService`` — the device flow (fusion-design §5, ported from the reference).

Endpoints map one-to-one to the frozen cli's expectations (the cli dials a
``base`` and hits ``base + /auth/device/{start,poll}`` and ``base + /agent``; we
mount the connector router at ``/connector`` so ``base = …/connector``):

* ``start(name)``                     → ``POST /connector/auth/device/start``
* ``approve(code, owner, agent)``     → ``POST /connector/connect`` (behind a login)
* ``poll(code)``                      → ``POST /connector/auth/device/poll``
* ``verify_token``                    → the ``/connector/agent`` ws + hook auth

Two invariants (fusion-design §4):
  * **approve is behind a human login** — ``approve`` takes the logged-in
    ``owner_user_id`` (injected at the trust boundary) and binds the device to it. The
    device is pure compute (no agent identity). There is no bare approve button.
  * **a token is necessary, not sufficient** — ``verify_token`` only identifies the
    device; the caller still authorizes the resolved actor against real permissions.
"""

import logging
import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.device.health import (
    DEFAULT_FAILURE_THRESHOLD,
    DEFAULT_QUARANTINE,
    Verdict,
    is_quarantined,
    judge_failure,
)
from app.domain.device.repository import (
    AuthCode,
    Device,
    DeviceRepository,
    HostHealth,
    Supply,
    Visibility,
)

logger = logging.getLogger(__name__)


class DeviceStatus:
    PENDING = "pending"
    APPROVED = "approved"


def _now() -> datetime:
    return datetime.now(UTC)


class DeviceService:
    def __init__(
        self,
        repo: DeviceRepository,
        *,
        code_ttl: timedelta = timedelta(minutes=10),
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._repo = repo
        self._code_ttl = code_ttl
        self._now = now

    async def start(self, device_name: str | None) -> str:
        """Begin a flow; return the opaque ``device_code`` the client polls on. The
        route wraps this with the ``approve_url`` (built from its request base).

        The cli often sends no name (``cheesehost auth login`` posts none), so fall
        back to a readable generated default — never the literal "unnamed". The human
        can still (re)name the node on the approval page or later."""
        code = AuthCode(
            code=uuid.uuid4().hex,
            device_name=(device_name or "").strip()
            or f"算力节点-{uuid.uuid4().hex[:6]}",
            status=DeviceStatus.PENDING,
            created_at=self._now(),
        )
        await self._repo.save_code(code)
        return code.code

    async def _live_code(self, code_value: str) -> AuthCode:
        entry = await self._repo.get_code(code_value)
        if entry is None:
            raise NotFoundError("Unknown or expired device code")
        if self._now() - entry.created_at > self._code_ttl:
            raise NotFoundError("Unknown or expired device code")
        return entry

    async def approve(
        self,
        code_value: str,
        *,
        owner_user_id: int,
        supply: Supply,
        visibility: Visibility,
        name: str | None = None,
    ) -> Device:
        """Approve a pending flow on behalf of the logged-in ``owner_user_id``, binding
        the device to that human as its owner and minting its durable token. The device
        is pure compute — it gets no agent identity (execution-architecture v3). The
        agent a screen runs as is resolved per project/topic at turn time.

        ``name`` is the human-chosen compute-node name from the approval page; blank
        keeps the name the cli proposed at start (avoids an "unnamed" node).
        Idempotent: approving an already-approved code returns the same device.

        ``supply`` and ``visibility`` have NO default on purpose (#282 决定 2 /
        #358). This is the one place a device is minted, so both enrolment entry
        points must name their answer here as a constant:
          * supply — the human device flow says ``self_hosted``, the MicroCloud
            sweep says ``cloud`` (入口决定待遇: never derived from what the machine
            looks like).
          * visibility — the human connector defaults to ``isolated`` unless the
            approver ticks 「让它看到整台机器」, because a person's own persistent box
            must not become whole-machine-visible by omission; MicroCloud says
            ``host`` (a fresh disposable VM is its own empty box — #358: Cloud
            collapses the visibility axis).
        A third entry point that forgets either is a pyright error, not a machine
        someone deletes by surprise a year later nor one silently exposed to the
        room."""
        entry = await self._live_code(code_value)
        if entry.status == DeviceStatus.APPROVED and entry.device_id is not None:
            existing = await self._repo.get_device(entry.device_id)
            if existing is not None:
                return existing
        chosen = (name or "").strip() or entry.device_name
        device = Device(
            device_id=uuid.uuid4().hex[:12],
            name=chosen,
            token=secrets.token_urlsafe(24),
            owner_user_id=owner_user_id,
            created_at=self._now(),
            supply=supply,
            visibility=visibility,
        )
        await self._repo.save_device(device)
        entry.status = DeviceStatus.APPROVED
        entry.device_id = device.device_id
        await self._repo.save_code(entry)
        return device

    async def poll(self, code_value: str) -> dict[str, str | None]:
        """What the polling client reads. ``{status}`` until approved; then the
        durable token + identity so the client can persist them."""
        entry = await self._live_code(code_value)
        if entry.status != DeviceStatus.APPROVED or entry.device_id is None:
            return {"status": entry.status}
        device = await self._repo.get_device(entry.device_id)
        if device is None:
            return {"status": DeviceStatus.PENDING}
        return {
            "status": DeviceStatus.APPROVED,
            "token": device.token,
            "device_id": device.device_id,
            "device_name": device.name,
        }

    async def verify_token(self, token: str) -> Device | None:
        """Identify the device behind a token (``X-Cheese-Session`` / bearer). Never
        sufficient on its own — the caller authorizes the resolved actor."""
        if not token:
            return None
        return await self._repo.get_device_by_token(token)

    async def get_device(self, device_id: str) -> Device | None:
        return await self._repo.get_device(device_id)

    async def code_device_name(self, code_value: str) -> str | None:
        """The human-proposed device name recorded on a pending code (so the approval
        page can show/prefill it). ``None`` if the code is unknown."""
        entry = await self._repo.get_code(code_value)
        return entry.device_name if entry is not None else None

    async def list_owned(self, owner_user_id: int) -> list[Device]:
        return await self._repo.list_devices_by_owner(owner_user_id)

    async def delete_owned(self, device_id: str, *, actor_user_id: int) -> None:
        """The HUMAN's door: an owner removing their own machine. Always allowed
        whatever the supply — 「我不想再把这台机器借给平台了」 is not a reclaim."""
        await self._require_owned(device_id, actor_user_id)
        await self._repo.delete_device(device_id)

    async def delete_platform_provisioned(
        self, device_id: str, *, actor_user_id: int
    ) -> None:
        """The PLATFORM's door: disposing of a machine cheese opened itself
        (#282 决定 2 的不变量).

        Every path where the platform destroys/reclaims compute on its own
        initiative must come through here, and it RAISES on a self-hosted device
        rather than skipping. The raise is the point: a reclaim path added later
        that forgets to ask about supply would otherwise delete a machine the
        platform never owned, and it would do so silently — the one failure mode
        #282 exists to prevent. A loud stop is recoverable; a deleted enrolment
        someone else was running work on is not."""
        device = await self._repo.get_device(device_id)
        if device is None:
            raise NotFoundError("device not found")
        if device.supply is not Supply.cloud:
            raise ForbiddenError(
                f"device {device_id} 的供给形式是 {device.supply}，"
                "平台不销毁不是自己开的机器（#282 供给形式不变量）"
            )
        await self.delete_owned(device_id, actor_user_id=actor_user_id)

    async def rename_owned(
        self, device_id: str, name: str, *, actor_user_id: int
    ) -> Device:
        """Rename a device the caller owns. The name is a human label only (never a
        semantic/authorization key), so a plain non-empty check is all that's needed."""
        device = await self._require_owned(device_id, actor_user_id)
        device.name = self._require_name(name)
        await self._repo.save_device(device)
        return device

    # -- device ↔ project assignment (the owner manages) -------------------

    async def assign_to_project(
        self, device_id: str, project_id: uuid.UUID, *, actor_user_id: int
    ) -> None:
        """Assign the device to a project. Only the device's owner may do this; the
        caller separately checks the owner is a member of that project."""
        await self._require_owned(device_id, actor_user_id)
        await self._repo.assign_project(device_id, project_id)

    async def unassign_from_project(
        self, device_id: str, project_id: uuid.UUID, *, actor_user_id: int
    ) -> None:
        await self._require_owned(device_id, actor_user_id)
        await self._repo.unassign_project(device_id, project_id)

    async def list_projects(self, device_id: str) -> list[uuid.UUID]:
        return await self._repo.list_project_ids(device_id)

    async def assign_to_team(
        self, device_id: str, team_id: int, *, actor_user_id: int
    ) -> None:
        """Bind the device to a team (为团队注册设备, v4): every project of that team
        may then run on it. Only the device's owner may bind; the caller separately
        checks the owner is a member of that team."""
        await self._require_owned(device_id, actor_user_id)
        await self._repo.assign_team(device_id, team_id)

    async def unassign_from_team(
        self, device_id: str, team_id: int, *, actor_user_id: int
    ) -> None:
        await self._require_owned(device_id, actor_user_id)
        await self._repo.unassign_team(device_id, team_id)

    async def list_teams(self, device_id: str) -> list[int]:
        return await self._repo.list_team_ids(device_id)

    async def list_devices_for_team(self, team_id: int) -> list[Device]:
        """The machines registered for a team (为团队注册设备, v4) — the team's compute.
        Every project of the team may run on these."""
        return await self._repo.list_devices_by_team(team_id)

    async def serves_project(self, device_id: str, project_id: uuid.UUID) -> bool:
        """Whether the device is assigned to the project — the precondition for
        running an agent (screen) there."""
        return await self._repo.is_assigned(device_id, project_id)

    async def list_devices_for_project(self, project_id: uuid.UUID) -> list[Device]:
        return await self._repo.list_devices_by_project(project_id)

    async def project_has_online_device(
        self, project_id: uuid.UUID, is_online: Callable[[str], bool]
    ) -> bool:
        """Whether this project has an enrolled machine connected right now — the
        honest availability of the self-hosted compute pool for THIS project's
        context (compute belongs to the project/team, not globally, v4)."""
        return any(
            is_online(d.device_id)
            for d in await self.list_devices_for_project(project_id)
        )

    # -- topic → device pin (affinity, execution-architecture v4) ----------

    async def topic_device(self, topic_id: uuid.UUID) -> str | None:
        """The device a topic is frozen to (``None`` before its first turn). Its work
        tree + resumable session live there; later turns must return to it."""
        return await self._repo.topic_device(topic_id)

    async def bind_topic_device(self, topic_id: uuid.UUID, device_id: str) -> None:
        """Pin a topic to the device its first turn ran on. Write-once — an existing
        pin is never overwritten (affinity is permanent for the topic's lifetime)."""
        await self._repo.bind_topic_device(topic_id, device_id)

    async def release_topic_device(self, topic_id: uuid.UUID, *, reason: str) -> None:
        """Drop a topic's pin so it can be re-pinned to another machine (#186 换身体).

        This is the ONLY sanctioned way past ``bind_topic_device``'s write-once rule,
        and it is deliberately a separate, reason-carrying call rather than a
        loosening of the resolver: a pin that can be overwritten silently is exactly
        the original drift bug, where a topic woke up on a different machine with an
        empty work tree and nobody could tell. The caller must also make the move
        visible in the room — see ``agent.host_swap``."""
        logger.warning("releasing topic %s device pin: %s", topic_id, reason)
        await self._repo.release_topic_device(topic_id)

    # -- machine health / quarantine (#186) --------------------------------

    async def host_health(self, device_id: str) -> HostHealth | None:
        return await self._repo.get_host_health(device_id)

    async def record_host_failure(
        self,
        device_id: str,
        code: str,
        *,
        threshold: int = DEFAULT_FAILURE_THRESHOLD,
        cooldown: timedelta = DEFAULT_QUARANTINE,
    ) -> Verdict:
        """Fold one HOST-SCOPED turn failure into the machine's health and return the
        verdict. Callers must only pass codes from a ``PlatformFailure`` whose
        ``host_scoped`` is true — a failure that would follow the topic to any machine
        says nothing about this one."""
        verdict = judge_failure(
            await self._repo.get_host_health(device_id),
            code,
            self._now(),
            threshold=threshold,
            cooldown=cooldown,
        )
        await self._repo.save_host_health(
            HostHealth(
                device_id=device_id,
                consecutive_failures=verdict.consecutive_failures,
                last_failure_code=verdict.last_failure_code,
                last_failure_at=verdict.last_failure_at,
                quarantined_until=verdict.quarantined_until,
            )
        )
        if verdict.quarantined:
            logger.warning(
                "device %s quarantined until %s after %s consecutive %s failures",
                device_id,
                verdict.quarantined_until,
                verdict.consecutive_failures,
                code,
            )
        return verdict

    async def record_host_success(self, device_id: str) -> None:
        """A turn got through on this machine — the streak is broken and any
        quarantine is moot. Deleting the row is the machine's way back into rotation
        without anyone having to clear it by hand."""
        await self._repo.clear_host_health(device_id)

    async def healthy_devices_for_project(
        self, project_id: uuid.UUID, is_online: Callable[[str], bool]
    ) -> list[Device]:
        """The project's machines that are online AND not quarantined — the pool a
        turn may actually be placed on."""
        devices = await self.list_devices_for_project(project_id)
        online = [d for d in devices if is_online(d.device_id)]
        if not online:
            return []
        health = await self._repo.list_host_health([d.device_id for d in online])
        now = self._now()
        return [d for d in online if not is_quarantined(health.get(d.device_id), now)]

    async def _require_owned(self, device_id: str, actor_user_id: int) -> Device:
        device = await self._repo.get_device(device_id)
        if device is None:
            raise NotFoundError("Unknown device")
        if device.owner_user_id != actor_user_id:
            raise ForbiddenError("Only the device owner may manage this device")
        return device

    @staticmethod
    def _require_name(name: str) -> str:
        clean = name.strip()
        if not clean:
            raise ValidationError("device name must not be empty")
        return clean


def device_service_for_session(session) -> DeviceService:
    """The DB-backed device service, assembled inside its own domain.

    Callers in other domains need a ``DeviceService`` bound to a session they
    already own, and the obvious way to get one — importing
    ``SqlDeviceRepository`` and wiring it up themselves — reaches across a domain
    boundary into another domain's data layer. ``tests/unit/
    test_domain_import_guard.py`` forbids exactly that, and it is right to: the
    repository is an implementation detail this domain gets to change. Building it
    here keeps the seam at the service.

    The repository import is deferred so importing the service module does not
    drag SQLAlchemy's mapper configuration in behind it.
    """
    from app.domain.device.sql_repository import SqlDeviceRepository

    return DeviceService(SqlDeviceRepository(session))
