"""Storage-agnostic device records + the repository contract.

The dataclasses are the domain shape ``DeviceService`` works with; a repository
(in-memory or SQL) converts to/from its own rows. Kept separate from the SQLAlchemy
models so the service can be unit-tested with the in-memory repo — no DB, no app.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# #282 四轴的两根词汇。定义搬去了 `device.supply`（跨领域要读它，而跨领域不许
# import 别人的 repository 模块——见 tests/unit/test_domain_import_guard.py）；
# 这里 re-export，域内调用点照旧。
from app.domain.device.supply import Supply


@dataclass
class Device:
    """An enrolled compute machine. ``owner_user_id`` is the human
    who approved it. A device is PURE COMPUTE (execution-architecture v3: a ComputePool
    node) — it carries NO agent identity; the agent a screen acts as is resolved per
    project/topic (fusion-design §5: agent = screen), not from the host machine."""

    device_id: str
    name: str
    owner_user_id: int | None
    created_at: datetime
    team_id: int
    # Supply is the stored lifecycle fact: who brought the machine in.
    supply: Supply = Supply.self_hosted
    # When the server last heard from it; None before it first connected.
    last_seen_at: datetime | None = None


@dataclass
class AuthCode:
    """A short-lived device-flow code. ``pending`` until a human approves it, then
    ``approved`` with the minted device's id recorded."""

    code: str
    device_name: str
    status: str  # "pending" | "approved"
    created_at: datetime
    device_id: str | None = None


@dataclass(frozen=True)
class SandboxCheck:
    """What a machine a person enrolled said, the last time it was asked,
    about giving a session a sandbox (`device.sandbox`). ``reason`` is its own
    account of why not, for its owner."""

    device_id: str
    isolates: bool
    reason: str | None
    checked_at: datetime


@dataclass
class HostHealth:
    """A machine's rolling failure streak + quarantine window (#186).

    Only written when something goes wrong; a successful turn deletes it. The
    judgement that turns this into "stop sending work here" is in ``health.py``
    (pure), so it can be exercised without a database."""

    device_id: str
    consecutive_failures: int = 0
    last_failure_code: str | None = None
    last_failure_at: datetime | None = None
    quarantined_until: datetime | None = None


class DeviceRepository(Protocol):
    """Data access for the device flow. Both the in-memory and SQL repos satisfy it."""

    async def save_code(self, code: AuthCode) -> None: ...
    async def get_code(self, code: str) -> AuthCode | None: ...
    async def delete_code(self, code: str) -> None: ...

    async def save_device(self, device: Device) -> None: ...
    async def note_last_seen(self, device_id: str, at: datetime) -> None: ...
    async def get_device(self, device_id: str) -> Device | None: ...
    async def get_devices(self, device_ids: Sequence[str]) -> dict[str, Device]: ...
    async def get_hosted_device(self, device_id: str) -> Device | None: ...
    async def get_device_by_token_digest(self, digest: str) -> Device | None: ...
    # Records the digest of the device's token unless it already has one;
    # whether it did. A token is issued once.
    async def set_token_digest(self, device_id: str, digest: str) -> bool: ...
    async def replace_token_digest(self, device_id: str, digest: str) -> None: ...
    async def delete_device(self, device_id: str) -> None: ...
    async def list_enrolled_by(self, owner_user_id: int) -> list[Device]: ...
    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]: ...
    async def serves_project(self, device_id: str, project_id: uuid.UUID) -> bool: ...
    async def list_devices_by_team(self, team_id: int) -> list[Device]: ...

    async def may_manage_machines(self, team_id: int, user_id: int) -> bool: ...

    # machine health (#186): failure streak + quarantine, one row per device,
    # absent when the machine is healthy.
    async def get_host_health(self, device_id: str) -> HostHealth | None: ...
    async def save_host_health(self, health: HostHealth) -> None: ...
    async def clear_host_health(self, device_id: str) -> None: ...
    async def list_host_health(
        self, device_ids: Sequence[str]
    ) -> dict[str, HostHealth]: ...

    # whether a machine a person enrolled can give a session a sandbox, as it
    # last answered (`device.sandbox`); one row per machine, absent until asked.
    async def save_sandbox_check(self, check: SandboxCheck) -> None: ...
    async def list_sandbox_checks(
        self, device_ids: Sequence[str]
    ) -> dict[str, SandboxCheck]: ...
