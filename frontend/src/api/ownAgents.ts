// Team model connections and their project teammate instances.
import type { OwnAgentsSettings } from '../types/ownAgents'

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
