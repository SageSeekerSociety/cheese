"""``DeviceService`` — the device flow, matching the frozen web-claude contract.

Endpoints (Act 2 step 3) map one-to-one:

* ``start(device_name)``            → ``POST /connector/auth/device/start``
* ``approve(code, actor, project)`` → ``POST /connector/connect`` (behind a login)
* ``poll(code)``                    → ``POST /connector/auth/device/poll``
* ``rename(token, name)``           → ``POST /connector/auth/device/rename``
* ``verify_token`` / ``resolve_agent`` — the ``/agent`` ws and ``cheese api`` auth.

Two invariants beyond the demo (architecture §3, §5.4):
  * **approve is behind a human login.** ``approve`` takes ``actor_user_id`` — the
    logged-in human injected at the trust boundary — and binds the device to a
    ``project_id`` and an ``agent_user_id``. The demo's bare button is replaced by
    this real, authorized binding.
  * **a token is necessary, not sufficient.** ``verify_token`` only identifies the
    device; the caller still authorizes the resolved actor against real permissions.
"""

import secrets
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from app.core.errors import BadRequestError, ForbiddenError, NotFoundError

from .repository import AuthCode, Device, DeviceRepository


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
        route wraps this with the ``approve_url`` (built from its request base)."""
        code = AuthCode(
            code=uuid.uuid4().hex,
            device_name=(device_name or "unnamed").strip() or "unnamed",
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

    async def approve(self, code_value: str, *, actor_user_id: int) -> Device:
        """Approve a pending flow on behalf of the logged-in ``actor_user_id``,
        binding the device to that human as its **owner** and minting its durable
        token. Project assignment and agent identity are separate concerns and are
        not decided here. Idempotent: approving an already-approved code returns the
        same device rather than minting a second one."""
        entry = await self._live_code(code_value)
        if entry.status == DeviceStatus.APPROVED and entry.device_id is not None:
            existing = await self._repo.get_device(entry.device_id)
            if existing is not None:
                return existing
        device = Device(
            device_id=uuid.uuid4().hex[:12],
            name=entry.device_name,
            token=secrets.token_urlsafe(24),
            owner_user_id=actor_user_id,
            created_at=self._now(),
        )
        await self._repo.save_device(device)
        entry.status = DeviceStatus.APPROVED
        entry.device_id = device.device_id
        await self._repo.save_code(entry)
        return device

    async def poll(self, code_value: str) -> dict[str, str | None]:
        """What the polling client reads. Returns only ``{status}`` until approved;
        then the durable token and identity so the client can persist them."""
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

    async def rename(self, token: str, device_name: str) -> Device:
        device = await self._require_device(token)
        name = device_name.strip()
        if not name:
            raise BadRequestError("device_name must not be empty")
        device.name = name
        await self._repo.save_device(device)
        return device

    async def get_device(self, device_id: str) -> Device | None:
        """Look up an enrolled device by id (e.g. to resolve a screen's project for
        viewer authorization). Read-only."""
        return await self._repo.get_device(device_id)

    async def list_owned(self, owner_user_id: int) -> list[Device]:
        """Every device this human owns (the 我的Agent page's device list)."""
        return await self._repo.list_devices_by_owner(owner_user_id)

    async def rename_owned(self, device_id: str, name: str, *, actor_user_id: int) -> Device:
        """Rename a device the caller owns (the owner-facing rename, keyed on
        device_id rather than the device's own token)."""
        device = await self._require_owned(device_id, actor_user_id)
        clean = name.strip()
        if not clean:
            raise BadRequestError("device name must not be empty")
        device.name = clean
        await self._repo.save_device(device)
        return device

    async def delete_owned(self, device_id: str, *, actor_user_id: int) -> None:
        """Delete a device the caller owns (and its project assignments)."""
        await self._require_owned(device_id, actor_user_id)
        await self._repo.delete_device(device_id)

    async def verify_token(self, token: str) -> Device | None:
        """Identify the device behind a token (``X-Cheese-Session`` / bearer). Never
        sufficient on its own — the caller authorizes the resolved actor."""
        if not token:
            return None
        return await self._repo.get_device_by_token(token)

    async def resolve_owner(self, token: str) -> int:
        """The human ``user_id`` who owns this device — the default actor for a
        device call made outside any screen (a call inside a screen acts as that
        screen's agent, resolved by the attribution seam)."""
        device = await self._require_device(token)
        return device.owner_user_id

    async def _require_device(self, token: str) -> Device:
        device = await self.verify_token(token)
        if device is None:
            raise NotFoundError("Unknown device token")
        return device

    # -- device ↔ project assignment (the owner manages) -------------------

    async def assign_to_project(self, device_id: str, project_id: int, *, actor_user_id: int) -> None:
        """Assign the device to a project. Only the device's owner may do this;
        the caller separately checks the owner is a member of that project."""
        await self._require_owned(device_id, actor_user_id)
        await self._repo.assign_project(device_id, project_id)

    async def unassign_from_project(
        self, device_id: str, project_id: int, *, actor_user_id: int
    ) -> None:
        await self._require_owned(device_id, actor_user_id)
        await self._repo.unassign_project(device_id, project_id)

    async def list_projects(self, device_id: str) -> list[int]:
        return await self._repo.list_project_ids(device_id)

    async def serves_project(self, device_id: str, project_id: int) -> bool:
        """Whether the device is assigned to the project — the precondition for
        running an agent (screen) there."""
        return await self._repo.is_assigned(device_id, project_id)

    async def list_devices_for_project(self, project_id: int) -> list[Device]:
        """Devices assigned to the project — any of them may run an agent there."""
        return await self._repo.list_devices_by_project(project_id)

    async def _require_owned(self, device_id: str, actor_user_id: int) -> Device:
        device = await self._repo.get_device(device_id)
        if device is None:
            raise NotFoundError("Unknown device")
        if device.owner_user_id != actor_user_id:
            raise ForbiddenError("Only the device owner may manage its projects")
        return device
