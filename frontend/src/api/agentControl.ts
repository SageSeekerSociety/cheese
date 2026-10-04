import type { AgentControlState } from '../types/agentControl'

import { request } from './http'

export type { AgentControlState } from '../types/agentControl'

export interface AgentControlResult {
  request_id: string
  status: string
  result: { response: { subtype: string; error?: string; response?: Record<string, unknown> } } | null
}

export function getAgentControl(topicId: string, agent?: string | null) {
  const query = agent ? `?agent=${encodeURIComponent(agent)}` : ''
  return request<AgentControlState>(`/topics/${encodeURIComponent(topicId)}/agent/control${query}`)
}

export function sendAgentControl(
  topicId: string,
  sessionId: string,
  control: Record<string, unknown>,
  requestId = crypto.randomUUID(),
  agent: string | null = null
) {
  return request<AgentControlResult>(`/topics/${encodeURIComponent(topicId)}/agent/control`, {
    method: 'POST',
    body: JSON.stringify({ session_id: sessionId, agent, request_id: requestId, request: control }),
  })
}
