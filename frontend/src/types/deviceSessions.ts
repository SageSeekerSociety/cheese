import type { ComputeChoice } from './compute'

// One agent session on a self-hosted device, as the bulk switch lists it; `working` = mid-turn, left alone.
export interface DeviceSession {
  id: string
  topic_id: string
  topic_title: string
  topic_title_source?: string
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
