"""In-memory ``DeviceRepository`` — for unit tests and the Phase-A spike.

Process-local dicts; no persistence. The SQL repo (``sql_repository``) is the
production implementation with the same contract.
"""

import uuid
from collections.abc import Sequence

from app.domain.device.repository import AuthCode, Device, HostHealth


class InMemoryDeviceRepository:
    def __init__(self) -> None:
        self._codes: dict[str, AuthCode] = {}
        self._devices: dict[str, Device] = {}
        self._topic_device: dict[uuid.UUID, str] = {}
        self._health: dict[str, HostHealth] = {}

    async def save_code(self, code: AuthCode) -> None:
        self._codes[code.code] = code

    async def get_code(self, code: str) -> AuthCode | None:
        return self._codes.get(code)

    async def save_device(self, device: Device) -> None:
        self._devices[device.device_id] = device

    async def get_device(self, device_id: str) -> Device | None:
        return self._devices.get(device_id)

    async def get_device_by_token(self, token: str) -> Device | None:
        for device in self._devices.values():
            if device.token == token:
                return device
        return None

    async def delete_device(self, device_id: str) -> None:
        self._devices.pop(device_id, None)

    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]:
        return [d for d in self._devices.values() if d.owner_user_id == owner_user_id]

    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]:
        return [d for d in self._devices.values() if project_id in d.project_ids]

    async def list_devices_by_team(self, team_id: int) -> list[Device]:
        return [d for d in self._devices.values() if team_id in d.team_ids]

    async def assign_project(self, device_id: str, project_id: uuid.UUID) -> None:
        device = self._devices.get(device_id)
        if device is not None and project_id not in device.project_ids:
            device.project_ids.append(project_id)

    async def unassign_project(self, device_id: str, project_id: uuid.UUID) -> None:
        device = self._devices.get(device_id)
        if device is not None and project_id in device.project_ids:
            device.project_ids.remove(project_id)

    async def list_project_ids(self, device_id: str) -> list[uuid.UUID]:
        device = self._devices.get(device_id)
        return list(device.project_ids) if device is not None else []

    async def is_assigned(self, device_id: str, project_id: uuid.UUID) -> bool:
        device = self._devices.get(device_id)
        return device is not None and project_id in device.project_ids

    async def assign_team(self, device_id: str, team_id: int) -> None:
        device = self._devices.get(device_id)
        if device is not None and team_id not in device.team_ids:
            device.team_ids.append(team_id)

    async def unassign_team(self, device_id: str, team_id: int) -> None:
        device = self._devices.get(device_id)
        if device is not None and team_id in device.team_ids:
            device.team_ids.remove(team_id)

    async def list_team_ids(self, device_id: str) -> list[int]:
        device = self._devices.get(device_id)
        return list(device.team_ids) if device is not None else []

    async def topic_device(self, topic_id: uuid.UUID) -> str | None:
        return self._topic_device.get(topic_id)

    async def bind_topic_device(self, topic_id: uuid.UUID, device_id: str) -> None:
        # write-once: an existing pin is permanent (affinity never drifts).
        self._topic_device.setdefault(topic_id, device_id)

    async def release_topic_device(self, topic_id: uuid.UUID) -> None:
        self._topic_device.pop(topic_id, None)

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
