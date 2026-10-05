import type { ComputeChoice } from './compute'

// One agent session on a self-hosted device, as the bulk switch lists it; `working` = mid-turn, left alone.
export interface DeviceSession {
  id: string
  topic_id: string
  topic_title: string
  topic_title_source?: string
  agent_handle: string
  agent_name: string
  agent_name_source?: string
  choice: ComputeChoice
  last_active: string
  working: boolean
}
