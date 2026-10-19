// 成员自己的 Claude Code（#2991）：项目允不允许成员接入、谁加入了，以及一台电脑上登录了没有。
import type { OwnAgentsSettings } from '../types/ownAgents'

import { connectorRequest } from './connector'
import { request } from './http'

function path(projectId: string): string {
  return `/projects/${encodeURIComponent(projectId)}/own-agents`
}

export function getOwnAgents(projectId: string): Promise<OwnAgentsSettings> {
  return request<OwnAgentsSettings>(path(projectId))
}

/** 用自己的一份模型连接把自己的 Claude Code 加入项目。 */
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

/** 把自己的一位 Claude Code 移出项目；只有它的主人能移。 */
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
