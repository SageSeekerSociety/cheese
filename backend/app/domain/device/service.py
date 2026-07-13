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

import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.core.errors import ForbiddenError, NotFoundError, ValidationError
from app.domain.device.repository import AuthCode, Device, DeviceRepository


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
        name: str | None = None,
    ) -> Device:
        """Approve a pending flow on behalf of the logged-in ``owner_user_id``, binding
        the device to that human as its owner and minting its durable token. The device
        is pure compute — it gets no agent identity (execution-architecture v3). The
        agent a screen runs as is resolved per project/topic at turn time.

        ``name`` is the human-chosen compute-node name from the approval page; blank
        keeps the name the cli proposed at start (avoids an "unnamed" node).
        Idempotent: approving an already-approved code returns the same device."""
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
        await self._require_owned(device_id, actor_user_id)
        await self._repo.delete_device(device_id)

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
