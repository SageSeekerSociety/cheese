// 机器页（`/admin/machines`）的 HTTP 层与形状。阶段、状态、排队时长都在服务端
// （`app/domain/machine/admin.py`）算好，这里只取。
import { request } from '@/api'

/** 宿主机走到它一生的哪一步。口径见后端 `host_phase`。 */
export type HostPhase =
  | 'creating'
  | 'enrolling'
  | 'enroll_failing'
  | 'ready'
  | 'idle'
  | 'offline'
  | 'error'
  | 'releasing'
  | 'failed'

export type SandboxState = 'waiting' | 'working' | 'idle' | 'stopping'

export interface MachineSandbox {
  id: string
  spec: string
  state: SandboxState
  project_id: string
  project: string | null
  room_id: string
  room: string | null
  placed_at: string | null
  waiting_since: string | null
  active_at: string | null
  stopped_at: string | null
  /** 最早什么时候因空闲被回收（下限）。 */
  idle_deadline: string | null
}

export interface MachineHost {
  id: string
  hostname: string
  machine_id: number | null
  whole_machine: boolean
  project: string | null
  phase: HostPhase
  status: string
  online: boolean
  ip: string | null
  cores: number
  memory_mb: number
  disk_gb: number
  slots: number
  used: number
  created_at: string | null
  enrolled_at: string | null
  last_seen_at: string | null
  idle_since: string | null
  offline_since: string | null
  failed_at: string | null
  released_at: string | null
  enroll_attempts: number
  enroll_error: string | null
  sandboxes: MachineSandbox[]
}

export interface WarmMachineRow {
  id: string
  state: string
  machine_id: number | null
  ip: string | null
  online: boolean
  created_at: string | null
  enrolled_at: string | null
  attempts: number
  error: string | null
}

export interface MachinesOverview {
  configured: boolean
  now: string
  pool: {
    hosts: number
    vms: number
    max_hosts: number
    at_cap: boolean
    slots: number
    used: number
    free: number
    min_free: number
    waiting: number
    oldest_wait_since: string | null
    provider_failures: number
    provider_failure_limit: number
    provider_window_min: number
    provider_probe_min: number
    last_provider_failure: string | null
    refusing: boolean
    warm_target: number
  }
  config: {
    slots_per_core: number
    sandbox_memory_mb: number
    sandbox_idle_s: number
    host_idle_hold_s: number
    vm_idle_s: number
  }
  hosts: MachineHost[]
  warm: WarmMachineRow[]
  history: {
    since: string
    prepared: number
    ready: number
    abandoned: number
    replaced: number
    released_idle: number
    released_unpaid: number
    wait_p50_s: number | null
    wait_p95_s: number | null
    wait_max_s: number | null
    /** 被拒的会话数，按原因分开；数的是会话，不是被拒的次数。 */
    refused_sessions: number
    refused_by_reason: Partial<Record<'pool_full' | 'provider_failing', number>>
    hosts_lost: number
    hosts_lost_by_reason: Partial<Record<'provider_error' | 'never_enrolled' | 'connector_away', number>>
    sandboxes_lost: number
  }
}

export function getMachines(): Promise<MachinesOverview> {
  return request<MachinesOverview>('/admin/stats/machines')
}
