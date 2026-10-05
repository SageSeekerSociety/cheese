import type { PoolListing } from '../cx_types'

// A room's work computer and the project's default one: the compute picker and
// the project settings read these (`GET /topics/{id}/compute-profile`,
// `GET /projects/{id}/compute-configs`).

// #282 §四 · whether an agent in this room can see a whole enrolled machine. `effective`: 'host' | 'isolated' | null
// (no agent on one); `machine_access` is the flag the room's 「能访问整台机器」 notice keys on (tooltip:
// `work.roomMachine.wholeMachineNotice`). `options`: the two 档 (isolated = default, host = the owner gives it).
export interface TopicComputeVisibility {
  options: PoolListing[]
  effective: 'host' | 'isolated' | null
  machine_access: boolean
}

export interface TopicComputeDevice {
  device_id: string
  name: string
  online: boolean
  owned?: boolean // the reader enrolled it, signed in: only they may give a room the whole machine
  sandbox_unavailable?: import('../lib/noticeText').NoticeMessage | null // why it cannot isolate a room
}

// GET /topics/{id}/compute-profile — the room's one work computer (一个话题一个容器, 2026-09-28).
// `choice` is what an agent that has not started yet will be given (room choice
// → project default → deployment default); `sessions` is each agent session and
// the machine it works on, `choice: null` for one that has not started working;
// `device_id` is the self-hosted machine pinned to this room, or null while
// 「系统挑一台」still waits for the first turn to choose one.
export interface TopicComputeProfile {
  choice: ComputeChoice
  project_default: ComputeChoice
  current: string
  device_id: string | null
  devices: TopicComputeDevice[]
  sessions: RoomSessionMachine[]
  profiles: PoolListing[]
  // Whether cloud also offers a whole VM per session (`whole_machine`).
  cloud_vm_available: boolean
  visibility: TopicComputeVisibility
}

// One agent session in the room and whether its agent can see a whole machine.
export interface RoomSessionMachine extends SessionWorkLease {
  machine_access: boolean
}

export interface ComputeChoice {
  name: string | null
  profile: 'cloud' | 'device'
  device_id: string | null
  // Cloud only: a whole virtual machine for each session instead of a sandbox.
  whole_machine?: boolean
}

export interface SessionWorkLease {
  id: string
  agent_handle: string
  harness: string
  choice: ComputeChoice | null
  // A cloud session's lease is only its status: the host its sandbox runs on is
  // the platform's, and is never named to users.
  lease: { device_id?: string; generation?: number; status: string; online: boolean } | null
}

// GET /projects/{id}/compute-configs — the machine new agents start on, and where
// the project's agents that have started are working now.
export interface ProjectComputeConfigs {
  default: ComputeChoice
  can_manage: boolean
  devices: TopicComputeDevice[]
  cloud_available: boolean
  cloud_vm_available: boolean
  distribution: ComputeDistribution
}

export interface ComputeDistribution {
  cloud: number
  cloud_vm: number
  devices: { device_id: string | null; name: string | null; agents: number; machine_access: boolean }[]
}
