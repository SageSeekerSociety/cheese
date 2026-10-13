// 「模型连接」（#2991）：列出成员自己的 Claude Code 在各台设备上的登录，从浏览器解除它，
// 或让一台在线的设备改用自定义模型服务（密钥交给设备，平台不保存）。
import type { ClaudeCodeLoginState, ModelConnection } from '../types/modelConnections'
import type { ModelServiceInput } from '../types/ownAgents'

import { connectorRequest } from './connector'

function claudeCode(deviceId: string): string {
  return `/my/devices/${encodeURIComponent(deviceId)}/claude-code`
}

export function listModelConnections(): Promise<{ connections: ModelConnection[] }> {
  return connectorRequest<{ connections: ModelConnection[] }>('/my/model-connections')
}

/** 解除：设备在线时它当场退出登录，不在线时等它下次连上；从这一刻起不再用它开会话。 */
export function revokeModelConnection(deviceId: string): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(claudeCode(deviceId), { method: 'DELETE' })
}

export function setModelService(deviceId: string, service: ModelServiceInput): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(`${claudeCode(deviceId)}/model-service`, {
    method: 'PUT',
    body: JSON.stringify({ base_url: service.baseUrl, token: service.token, model: service.model }),
  })
}
