"""In-memory ``DeviceRepository`` — for unit tests and the Phase-A spike.

Process-local dicts; no persistence. The SQL repo (``sql_repository``) is the
production implementation with the same contract.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

from app.domain.device.repository import (
    AuthCode,
    Device,
    HostHealth,
    SandboxCheck,
)
from app.domain.device.supply import Supply


class InMemoryDeviceRepository:
    def __init__(self) -> None:
        self.team_admins: dict[int, set[int]] = {}
        self.project_teams: dict[uuid.UUID, int] = {}
        self._codes: dict[str, AuthCode] = {}
        self._devices: dict[str, Device] = {}
        self._digests: dict[str, str] = {}
        self._hosted_device_ids: set[str] = set()
        self._health: dict[str, HostHealth] = {}
        self._sandbox: dict[str, SandboxCheck] = {}

    async def save_code(self, code: AuthCode) -> None:
        self._codes[code.code] = code

    async def get_code(self, code: str) -> AuthCode | None:
        return self._codes.get(code)

    async def delete_code(self, code: str) -> None:
        self._codes.pop(code, None)

    async def save_device(self, device: Device) -> None:
        self._devices[device.device_id] = device
        if device.supply is Supply.self_hosted:
            self._hosted_device_ids.add(device.device_id)

    async def get_device(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    async def get_devices(self, device_ids: Sequence[str]) -> dict[str, Device]:
        found: dict[str, Device] = {}
        for device_id in device_ids:
            device = self._devices.get(device_id)
            if device is not None:
                found[device_id] = device
        return found

    async def note_last_seen(self, device_id: str, at: datetime) -> None:
        device = self._devices.get(device_id)
        if device is not None:
            device.last_seen_at = at

    async def get_hosted_device(self, device_id: str) -> Device | None:
        if device_id not in self._hosted_device_ids:
            return None
        return self._devices.get(device_id)

    async def get_device_by_token_digest(self, digest: str) -> Device | None:
        for device_id, held in self._digests.items():
            if held == digest:
                return self._devices.get(device_id)
        return None

    async def set_token_digest(self, device_id: str, digest: str) -> bool:
        if device_id not in self._devices or device_id in self._digests:
            return False
        self._digests[device_id] = digest
        return True

    async def replace_token_digest(self, device_id: str, digest: str) -> None:
        if device_id in self._devices:
            self._digests[device_id] = digest

    async def delete_device(self, device_id: str) -> None:
        self._devices.pop(device_id, None)
        self._digests.pop(device_id, None)
        self._hosted_device_ids.discard(device_id)

    async def list_enrolled_by(self, owner_user_id: int) -> list[Device]:
        return [
            d
            for d in self._devices.values()
            if d.device_id in self._hosted_device_ids
            and d.owner_user_id == owner_user_id
        ]

    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]:
        return [
            d
            for d in self._devices.values()
            if d.device_id in self._hosted_device_ids
            and d.team_id == self.project_teams.get(project_id)
        ]

    async def serves_project(self, device_id: str, project_id: uuid.UUID) -> bool:
        return any(
            d.device_id == device_id
            for d in await self.list_devices_by_project(project_id)
        )

    async def list_devices_by_team(self, team_id: int) -> list[Device]:
        return [
            d
            for d in self._devices.values()
            if d.device_id in self._hosted_device_ids and team_id == d.team_id
        ]

    async def may_manage_machines(self, team_id: int, user_id: int) -> bool:
        return user_id in self.team_admins.get(team_id, set())

    # -- machine health (#186) ---------------------------------------------

    async def get_host_health(self, device_id: str) -> HostHealth | None:
        return self._health.get(device_id)

    async def save_host_health(self, health: HostHealth) -> None:
        self._health[health.device_id] = health

    async def clear_host_health(self, device_id: str) -> None:
        self._health.pop(device_id, None)

    async def list_host_health(
        self, device_ids: Sequence[str]
    ) -> dict[str, HostHealth]:
        return {
            d: self._health[d] for d in device_ids if self._health.get(d) is not None
        }

    async def save_sandbox_check(self, check: SandboxCheck) -> None:
        self._sandbox[check.device_id] = check

    async def list_sandbox_checks(
        self, device_ids: Sequence[str]
    ) -> dict[str, SandboxCheck]:
        return {d: self._sandbox[d] for d in device_ids if d in self._sandbox}
