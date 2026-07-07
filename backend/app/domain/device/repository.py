"""Storage seam for the device flow.

Entities are plain dataclasses so the service logic is storage-agnostic; the
``DeviceRepository`` Protocol is the only thing the service depends on. Act 3
adds a SQLAlchemy-backed repository implementing the same Protocol — the service
and tests are untouched.

Security note for the real repository: persist only a **hash** of the device
token (and of nothing else secret). ``InMemoryDeviceRepository`` keeps the raw
token because it is a single-process test/demo store; a DB repository must not.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class AuthCode:
    """A short-lived device-flow code, created by ``start`` and resolved by
    ``approve`` / ``poll``. ``pending`` until a human approves; then it carries the
    minted ``device_id`` so the polling client can pick up its durable token."""

    code: str
    device_name: str
    status: str  # "pending" | "approved"
    created_at: datetime
    device_id: str | None = None


@dataclass
class Device:
    """An enrolled client machine. Bound at approval time to exactly one **owner**
    (the human ``user_id`` who approved it). A device is later assigned to projects
    (many-to-many) and runs one agent per screen — neither project nor agent
    identity lives on the device."""

    device_id: str
    name: str
    token: str
    owner_user_id: int
    created_at: datetime


class DeviceRepository(Protocol):
    async def save_code(self, code: AuthCode) -> None: ...
    async def get_code(self, code: str) -> AuthCode | None: ...
    async def save_device(self, device: Device) -> None: ...
    async def get_device(self, device_id: str) -> Device | None: ...
    async def get_device_by_token(self, token: str) -> Device | None: ...
    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]: ...
    async def delete_device(self, device_id: str) -> None: ...
    # Device ↔ project assignment (many-to-many; the owner assigns).
    async def assign_project(self, device_id: str, project_id: int) -> None: ...
    async def unassign_project(self, device_id: str, project_id: int) -> None: ...
    async def list_project_ids(self, device_id: str) -> list[int]: ...
    async def is_assigned(self, device_id: str, project_id: int) -> bool: ...


class InMemoryDeviceRepository:
    """Single-process store for tests and the demo. Not for production (holds raw
    tokens, no expiry sweep — the service enforces code TTL on read)."""

    def __init__(self) -> None:
        self._codes: dict[str, AuthCode] = {}
        self._devices: dict[str, Device] = {}
        self._by_token: dict[str, Device] = {}
        self._assignments: dict[str, set[int]] = {}

    async def save_code(self, code: AuthCode) -> None:
        self._codes[code.code] = code

    async def get_code(self, code: str) -> AuthCode | None:
        return self._codes.get(code)

    async def save_device(self, device: Device) -> None:
        self._devices[device.device_id] = device
        self._by_token[device.token] = device

    async def get_device(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    async def get_device_by_token(self, token: str) -> Device | None:
        return self._by_token.get(token)

    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]:
        return [d for d in self._devices.values() if d.owner_user_id == owner_user_id]

    async def delete_device(self, device_id: str) -> None:
        device = self._devices.pop(device_id, None)
        if device is not None:
            self._by_token.pop(device.token, None)
        self._assignments.pop(device_id, None)

    async def assign_project(self, device_id: str, project_id: int) -> None:
        self._assignments.setdefault(device_id, set()).add(project_id)

    async def unassign_project(self, device_id: str, project_id: int) -> None:
        self._assignments.get(device_id, set()).discard(project_id)

    async def list_project_ids(self, device_id: str) -> list[int]:
        return sorted(self._assignments.get(device_id, set()))

    async def is_assigned(self, device_id: str, project_id: int) -> bool:
        return project_id in self._assignments.get(device_id, set())
