// Team model connections and their project teammate instances.
import type { OwnAgentsSettings } from '../types/ownAgents'

import { connectorRequest } from './connector'
import { request } from './http'

function path(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/own-agents`
}

export function getOwnAgents(projectId: string): Promise<OwnAgentsSettings> {
  return request<OwnAgentsSettings>(path(projectId))
}

/** Add a teammate with a team connection, model, and execution device. */
export function addOwnAgent(
  projectId: string,
  connectionId: string,
  model: string,
  deviceId: string
): Promise<OwnAgentsSettings> {
  return request<OwnAgentsSettings>(path(projectId), {
    method: 'POST',
    body: JSON.stringify({ connection_id: connectionId, model, device_id: deviceId }),
  })
}

/** Remove a teammate from the project. */
export function removeOwnAgent(projectId: string, handle: string): Promise<OwnAgentsSettings> {
  return request<OwnAgentsSettings>(`${path(projectId)}/${encodeURIComponent(handle)}`, {
    method: 'DELETE',
  })
}

/** 让这台电脑再报一次机主的 Claude Code 登录状态：机主在电脑上登录，服务器并不知道。 */
export function checkClaudeCode(deviceId: string): Promise<void> {
  return connectorRequest<void>(`/my/devices/${encodeURIComponent(deviceId)}/claude-code`, {
    method: 'POST',
  })
}
