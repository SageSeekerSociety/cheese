"""The shape of the admin machines page's answer (``admin.overview``), for the
API's OpenAPI document and the frontend types generated from it."""

from pydantic import BaseModel


class MachineSandboxOut(BaseModel):
    id: str
    spec: str
    state: str
    project_id: str
    project: str | None
    room_id: str
    room: str | None
    placed_at: str | None
    waiting_since: str | None
    active_at: str | None
    stopped_at: str | None
    idle_deadline: str | None


class MachineHostOut(BaseModel):
    id: str
    hostname: str
    machine_id: int | None
    whole_machine: bool
    project: str | None
    phase: str
    status: str
    online: bool
    ip: str | None
    cores: int
    memory_mb: int
    disk_gb: int
    slots: int
    used: int
    created_at: str | None
    enrolled_at: str | None
    last_seen_at: str | None
    idle_since: str | None
    offline_since: str | None
    failed_at: str | None
    released_at: str | None
    enroll_attempts: int
    enroll_error: str | None
    sandboxes: list[MachineSandboxOut]


class WarmMachineOut(BaseModel):
    id: str
    state: str
    machine_id: int | None
    ip: str | None
    online: bool
    created_at: str | None
    enrolled_at: str | None
    attempts: int
    error: str | None


class MachinePoolOut(BaseModel):
    hosts: int
    vms: int
    max_hosts: int
    at_cap: bool
    slots: int
    used: int
    free: int
    min_free: int
    waiting: int
    oldest_wait_since: str | None
    provider_failures: int
    provider_failure_limit: int
    provider_window_min: int
    provider_probe_min: int
    last_provider_failure: str | None
    refusing: bool
    warm_target: int


class MachineConfigOut(BaseModel):
    slots_per_core: int
    sandbox_memory_mb: int
    sandbox_idle_s: int
    host_idle_hold_s: int
    vm_idle_s: int


class MachineHistoryOut(BaseModel):
    since: str
    prepared: int
    ready: int
    abandoned: int
    replaced: int
    released_idle: int
    released_unpaid: int
    wait_p50_s: float | None
    wait_p95_s: float | None
    wait_max_s: float | None


class MachinesOverviewOut(BaseModel):
    configured: bool
    now: str
    pool: MachinePoolOut
    config: MachineConfigOut
    hosts: list[MachineHostOut]
    warm: list[WarmMachineOut]
    history: MachineHistoryOut
