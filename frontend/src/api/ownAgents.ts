// 成员自己的 Claude Code（#2991）：项目允不允许成员接入、谁接了，以及一台电脑上登录了没有。
import type { ClaudeCodeLogin, OwnAgentsSettings } from '../types/ownAgents'

import { connectorRequest } from './connector'
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

/** 让这台电脑再报一次机主的 Claude Code 登录状态：机主在电脑上登录，服务器并不知道。 */
export function checkClaudeCode(deviceId: string): Promise<ClaudeCodeLogin | null> {
  return connectorRequest<ClaudeCodeLogin | null>(`/my/devices/${encodeURIComponent(deviceId)}/claude-code`, {
    method: 'POST',
  })
}
