"""Storage-agnostic device records + the repository contract.

The dataclasses are the domain shape ``DeviceService`` works with; a repository
(in-memory or SQL) converts to/from its own rows. Kept separate from the SQLAlchemy
models so the service can be unit-tested with the in-memory repo — no DB, no app.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

# #282 四轴的两根词汇。定义搬去了 `device.supply`（跨领域要读它，而跨领域不许
# import 别人的 repository 模块——见 tests/unit/test_domain_import_guard.py）；
# 这里 re-export，域内调用点照旧。
from app.domain.device.supply import Supply, Visibility


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
    # Supply remains the stored lifecycle fact. Visibility is the legacy device
    # column retained for the additive #442 dual-read window; hosted resolution uses
    # TopicDevice.visibility instead.
    supply: Supply = Supply.self_hosted
    visibility: Visibility = Visibility.isolated
    # The device's own ccproxy identity (`user:password`), or None. Mirrors the
    # model column (b7c4e9a20d13): a self-hosted device that brings its own
    # ccproxy ticket runs on the machine-ticket model, and the provider reads
    # THIS to decide whether to signal the launcher. It has to live on the
    # dataclass, not just the row — `get_device` returns this, and reading the
    # attribute off a dataclass that lacked it silently returned None, so the
    # signal was never sent and the dev box looped on the swap path (2026-08-15).
    ccproxy_upstream: str | None = None
    # The id ccproxy knows this device by (#420), or None. The revocation
    # handle: deleting a device that carries one must first get ccproxy's
    # confirmed `DELETE /machine/{id}` — see `DeviceService._revoke_ccproxy`.
    ccproxy_machine_id: int | None = None


@dataclass
class AuthCode:
    """A short-lived device-flow code. ``pending`` until a human approves it, then
    ``approved`` with the minted device's id recorded."""

    code: str
    device_name: str
    status: str  # "pending" | "approved"
    created_at: datetime
    device_id: str | None = None


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


@dataclass(frozen=True, slots=True)
class TopicDevice:
    """A topic's machine binding, including that topic's access boundary."""

    topic_id: uuid.UUID
    device_id: str
    visibility: Visibility


class DeviceRepository(Protocol):
    """Data access for the device flow. Both the in-memory and SQL repos satisfy it."""

    async def save_code(self, code: AuthCode) -> None: ...
    async def get_code(self, code: str) -> AuthCode | None: ...

    async def save_device(self, device: Device) -> None: ...
    async def get_device(self, device_id: str) -> Device | None: ...
    async def get_hosted_device(self, device_id: str) -> Device | None: ...
    async def get_device_by_token(self, token: str) -> Device | None: ...
    async def delete_device(self, device_id: str) -> None: ...
    async def list_devices_by_owner(self, owner_user_id: int) -> list[Device]: ...
    async def list_devices_by_project(self, project_id: uuid.UUID) -> list[Device]: ...
    async def list_cloud_devices_by_project(
        self, project_id: uuid.UUID
    ) -> list[Device]: ...
    async def list_devices_by_team(self, team_id: int) -> list[Device]: ...

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
    async def topic_binding(self, topic_id: uuid.UUID) -> TopicDevice | None: ...
    async def bind_topic_device(
        self, topic_id: uuid.UUID, device_id: str, visibility: Visibility
    ) -> None: ...
    # Drop a topic's pin so it can be re-pinned elsewhere. The ONLY way past
    # write-once: an explicit, reasoned release (#186 换身体), never a silent
    # fallback inside the resolver — that was the original drift bug.
    async def release_topic_device(self, topic_id: uuid.UUID) -> None: ...

    # machine health (#186): failure streak + quarantine, one row per device,
    # absent when the machine is healthy.
    async def get_host_health(self, device_id: str) -> HostHealth | None: ...
    async def save_host_health(self, health: HostHealth) -> None: ...
    async def clear_host_health(self, device_id: str) -> None: ...
    async def list_host_health(
        self, device_ids: Sequence[str]
    ) -> dict[str, HostHealth]: ...
