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

/** 一个沙箱最近一次的读数：内存每分钟读一次，家目录每次走盘（几分钟一次）读一次。 */
export interface SandboxUsage {
  memory_bytes: number | null
  /** 沙箱起来以来内存用到的最多。 */
  memory_peak_bytes: number | null
  /** 其中进程自己的（匿名）和页缓存各多少。 */
  anon_bytes: number | null
  file_bytes: number | null
  /** 用了宿主机多少交换文件：`memory.swap.max` 为 0 时一直是 0。 */
  swap_bytes: number | null
  /** 起来以来被内核因内存不足杀掉几次。 */
  oom_kills: number | null
  memory_read_at: string | null
  disk_bytes: number | null
  disk_inodes: number | null
  disk_read_at: string | null
}

/** 一台宿主机最近一次读数，和之前几小时内存、CPU 每一段里的最高值（缺数为 null）。 */
export interface HostUsage {
  read_at: string
  disk_bytes: number
  disk_used_bytes: number
  inodes: number | null
  inodes_used: number | null
  memory_bytes: number | null
  memory_used_bytes: number | null
  /** 宿主机数据盘上的交换文件；没建时为 null。 */
  swap_bytes: number | null
  swap_used_bytes: number | null
  /** 内存压力（PSI）：最近 1 分钟、5 分钟里因内存卡住的时间占比（百分数）。 */
  memory_pressure_some_avg10: number | null
  memory_pressure_some_avg60: number | null
  memory_pressure_full_avg10: number | null
  memory_pressure_full_avg60: number | null
  /** 是否加载了 zswap。 */
  zswap: boolean | null
  /** 两次读数之间占满了几成核（0–1）；只读到一次时为 null。 */
  cpu_busy: number | null
  load1: number | null
  sandboxes: number | null
  memory_history: (number | null)[]
  cpu_history: (number | null)[]
}

export interface MachineSandbox {
  id: string
  spec: string
  state: SandboxState
  project_id: string | null
  project: string | null
  room_id: string
  room: string | null
  placed_at: string | null
  waiting_since: string | null
  active_at: string | null
  stopped_at: string | null
  /** 最早什么时候因空闲被回收（下限）。 */
  idle_deadline: string | null
  /** 还没读到过时为 null。 */
  usage: SandboxUsage | null
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
  /** 建这台时所装的镜像版本；旧行（和温机）为 null，即不知道。 */
  image_version: string | null
  /** 和现在会建的那一版是否不同；问不到 provider、或是整机虚拟机时为 null。 */
  image_outdated: boolean | null
  /** 正在滚动重建：不再接新沙箱，等它空了就释放、换一台新的。 */
  draining: boolean
  draining_since: string | null
  enroll_attempts: number
  enroll_error: string | null
  /** 还没读到过（没注册、不在线、刚起来）时为 null。 */
  usage: HostUsage | null
  sandboxes: MachineSandbox[]
}

export interface WarmMachineRow {
  id: string
  state: string
  machine_id: number | null
  hostname: string | null
  cores: number | null
  memory_mb: number | null
  disk_gb: number | null
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
    /** 固定常开的普通宿主机数；0 表示纯按需扩缩。 */
    fixed_hosts: number
    warm_target: number
  }
  config: {
    slots_per_core: number
    sandbox_memory_mb: number
    sandbox_idle_s: number
    host_idle_hold_s: number
    /** 固定池里宿主机只按年龄重建的天数；0 表示年龄不触发重建。 */
    rebuild_after_days: number
    vm_idle_s: number
    sandbox_disk_budget_gb: number
    sandbox_inode_budget: number
    /** 沙箱用到上限的这一份就标出来。 */
    near_cap: number
    /** 宿主机多久读一次、多久走一次盘（秒）。 */
    check_s: number
    /** 宿主机用过这一份就告警（两次读数都过）。 */
    alert_used: Record<'disk' | 'inodes' | 'memory', number>
    history_step_s: number
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
