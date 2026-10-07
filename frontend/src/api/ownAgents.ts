// 项目里成员自己的 Claude Code（#2991）：项目允不允许成员接入，以及谁接了。
import type { OwnAgentsSettings } from '../types/ownAgents'

import { request } from './http'

function path(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/own-agents`
}

export function getOwnAgents(projectId: string): Promise<OwnAgentsSettings> {
  return request<OwnAgentsSettings>(path(projectId))
}

export function setOwnAgentsAllowed(projectId: string, allowed: boolean): Promise<OwnAgentsSettings> {
  return request<OwnAgentsSettings>(path(projectId), {
    method: 'PUT',
    body: JSON.stringify({ allowed }),
  })
}
