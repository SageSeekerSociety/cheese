// ---- self-hosted 设备连接器 (P3 Phase B) ----

// 这一节的类型原来长在 `@/cx_types` 里，现在跟本模块已有的那两个设备函数合住；
// `@/cx_types` 把它们原样转出去，所以对外一个名字都没变。只认 props、不连网络层
// 的那半张设备画面照旧从 `MyDevice` 取类型 —— 这台设备的样子本来就写在手写的接口
// 契约里，搬过来之后 `@/types/devices` 和 `@/cx_types` 上的名字还是同一份。
import type { DeviceScreen } from './deviceSessions'

// A compute machine (算力节点) the signed-in human enrolled. A device is pure compute
// — it has NO agent identity; the agents running on it are `screens` (each carries its
// own agent). See execution-architecture v3 / fusion-design §5.
export interface MyDevice {
  device_id: string
  name: string
  online: boolean
  team_id: number
  logins?: import('./modelConnections').ClaudeCodeLoginState[]
  screens: DeviceScreen[]
  // Who works on this machine now (each session whose work computer it is), for its owner only.
  in_use?: DeviceUser[] | null
}

export interface DeviceUser {
  project_id: string
  project_name: string
  topic_id: string
  topic_title: string
  agent_handle: string
  agent_name: string
  agent_name_source?: string
}

// A team the signed-in user belongs to (GET /teams/my-teams) — trimmed to what
// the device pages need. Includes the auto-provisioned personal team (个人 =
// 单人真团队), which the backend sorts first and flags `personal`.
export interface MyTeam {
  id: number
  handle: string
  name: string
  personal?: boolean
  role?: string
}

// The result of approving a pending device flow (binds the machine to its owner).
export interface DeviceApproval {
  device_id: string
  device_name: string
  team_id: number
}

// 设备列表里一台设备上次在线的时间：链路断开时记下的最后一次收到消息的时刻，还没断开过时为 null。
export function lastSeenOf(device: MyDevice): string | null {
  return (device as MyDevice & { last_seen_at?: string | null }).last_seen_at ?? null
}

/** 这台设备的系统（`windows`、`darwin`、`linux`），它的连接程序上次说的；没连上过时为 null。 */
export function systemOf(device: MyDevice): string | null {
  return (device as MyDevice & { system?: string | null }).system ?? null
}
