import type { ComputeChoice } from './compute'

// One agent session on a self-hosted device, as the bulk switch lists it; `working` = mid-turn, left alone.
export interface DeviceSession {
  id: string
  topic_id: string
  topic_title: string
  // A task's own session: it moves with the task's work computer, not the room's.
  task_id?: string | null
  task_title?: string | null
  agent_handle: string
  agent_name: string
  agent_name_source?: string
  choice: ComputeChoice
  last_active: string
  working: boolean
}

// One agent (a screen) currently running on an enrolled device — a live 现场 the
// browser can watch read-only via `screenWsUrl(sid)`.
export interface DeviceScreen {
  sid: string
  agent_handle: string
  agent_user_id: string
  project_id: string | null
  topic_id: string | null
  // The name its agent goes by, resolved when the list is read; null when the
  // screen names no room. `agent_name_source` as on a roster row.
  agent_name?: string | null
  agent_name_source?: string | null
}
