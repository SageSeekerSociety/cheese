"""Storage-agnostic device records + the repository contract.

The dataclasses are the domain shape ``DeviceService`` works with; a repository
(in-memory or SQL) converts to/from its own rows. Kept separate from the SQLAlchemy
models so the service can be unit-tested with the in-memory repo — no DB, no app.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class Device:
    """An enrolled compute machine + its durable token. ``owner_user_id`` is the human
    who approved it. A device is PURE COMPUTE (execution-architecture v3: a ComputePool
    node) — it carries NO agent identity; the agent a screen acts as is resolved per
    project/topic (fusion-design §5: agent = screen), not from the host machine."""

    device_id: str
    name: str
    token: str
    owner_user_id: int
    created_at: datetime
    project_ids: list[uuid.UUID] = field(default_factory=list)
    # Teams this device is bound to (为团队注册设备, v4): every project of these
    # teams may run on it. Empty = personal (usable only via explicit project assign).
    team_ids: list[int] = field(default_factory=list)


@dataclass
class AuthCode:
    """A short-lived device-flow code. ``pending`` until a human approves it, then
    ``approved`` with the minted device's id recorded."""

    code: str
    device_name: str
    status: str  # "pending" | "approved"
    created_at: datetime
    device_id: str | None = None


class DeviceRepository(Protocol):
    """Data access for the device flow. Both the in-memory and SQL repos satisfy it."""

    async def save_code(self, code: AuthCode) -> None: ...
    async def get_code(self, code: str) -> AuthCode | None: ...

    async def save_device(self, device: Device) -> None: ...
    async def get_device(self, device_id: str) -> Device | None: ...
    async def get_device_by_token(self, token: str) -> Device | None: ...
    async def delete_device(self, device_id: str) -> None: ...
    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]: ...
    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]: ...

    async def assign_project(self, device_id: str, project_id: uuid.UUID) -> None: ...
    async def unassign_project(self, device_id: str, project_id: uuid.UUID) -> None: ...
    async def list_project_ids(self, device_id: str) -> list[uuid.UUID]: ...
    async def is_assigned(self, device_id: str, project_id: uuid.UUID) -> bool: ...

    # device↔team bindings (为团队注册设备, v4): a device bound to a team is usable by
    # every project of that team. ``assign`` is idempotent (an existing bind is kept).
    async def assign_team(self, device_id: str, team_id: int) -> None: ...
    async def unassign_team(self, device_id: str, team_id: int) -> None: ...
    async def list_team_ids(self, device_id: str) -> list[int]: ...

    # topic→device pin (affinity, v4): the device a topic is frozen to on its
    # first turn. ``bind`` is write-once — an existing pin is never overwritten.
    async def topic_device(self, topic_id: uuid.UUID) -> str | None: ...
    async def bind_topic_device(self, topic_id: uuid.UUID, device_id: str) -> None: ...
