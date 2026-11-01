"""The shape of the admin machines page's answer (``admin.overview``), for the
API's OpenAPI document and the frontend types generated from it."""

from pydantic import BaseModel


class SandboxUsageOut(BaseModel):
    memory_bytes: int | None
    memory_peak_bytes: int | None
    anon_bytes: int | None
    file_bytes: int | None
    swap_bytes: int | None
    oom_kills: int | None
    memory_read_at: str | None
    disk_bytes: int | None
    disk_inodes: int | None
    disk_read_at: str | None


class HostUsageOut(BaseModel):
    read_at: str
    disk_bytes: int
    disk_used_bytes: int
    inodes: int | None
    inodes_used: int | None
    memory_bytes: int | None
    memory_used_bytes: int | None
    swap_bytes: int | None
    swap_used_bytes: int | None
    memory_pressure_some_avg10: float | None
    memory_pressure_some_avg60: float | None
    memory_pressure_full_avg10: float | None
    memory_pressure_full_avg60: float | None
    zswap: bool | None
    cpu_busy: float | None
    load1: float | None
    sandboxes: int | None
    memory_history: list[float | None]
    cpu_history: list[float | None]


class MachineSandboxOut(BaseModel):
    id: str
    spec: str
    state: str
    project_id: str | None
    project: str | None
    room_id: str
    room: str | None
    placed_at: str | None
    waiting_since: str | None
    active_at: str | None
    stopped_at: str | None
    idle_deadline: str | None
    usage: SandboxUsageOut | None


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
    image_version: str | None
    image_outdated: bool | None
    draining: bool
    draining_since: str | None
    enroll_attempts: int
    enroll_error: str | None
    usage: HostUsageOut | None
    sandboxes: list[MachineSandboxOut]


class WarmMachineOut(BaseModel):
    id: str
    state: str
    machine_id: int | None
    hostname: str | None
    cores: int | None
    memory_mb: int | None
    disk_gb: int | None
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
    fixed_hosts: int
    warm_target: int


class MachineConfigOut(BaseModel):
    slots_per_core: int
    sandbox_memory_mb: int
    sandbox_idle_s: int
    host_idle_hold_s: int
    rebuild_after_days: int
    vm_idle_s: int
    sandbox_disk_budget_gb: int
    sandbox_inode_budget: int
    near_cap: float
    check_s: int
    alert_used: dict[str, float]
    history_step_s: int


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
    refused_sessions: int
    refused_by_reason: dict[str, int]
    hosts_lost: int
    hosts_lost_by_reason: dict[str, int]
    sandboxes_lost: int


class MachinesOverviewOut(BaseModel):
    configured: bool
    now: str
    pool: MachinePoolOut
    config: MachineConfigOut
    hosts: list[MachineHostOut]
    warm: list[WarmMachineOut]
    history: MachineHistoryOut
