import type {
  AccountConnection,
  AccountConnectionInput,
  ClaudeCodeLoginState,
  ModelConnections,
} from '../types/modelConnections'

import { connectorRequest } from './connector'

const root = (teamId: number) => `/teams/${teamId}/model-connections`

function login(teamId: number, loginId: string): string {
  return `/teams/${teamId}/claude-logins/${encodeURIComponent(loginId)}`
}

function held(teamId: number, id: string): string {
  return `${root(teamId)}/${encodeURIComponent(id)}`
}

export function listModelConnections(teamId: number): Promise<ModelConnections> {
  return connectorRequest<ModelConnections>(root(teamId))
}

/** 再问一次这份登录所在的设备；设备一次答出它上面的每一份。 */
export function checkModelConnection(teamId: number, loginId: string): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(`${login(teamId, loginId)}/check`, { method: 'POST' })
}

/** 解除：设备在线时它当场退出这份登录，不在线时等它下次连上；从这一刻起不再用它开会话。同一台设备上的其他登录不受影响。 */
export function revokeModelConnection(teamId: number, loginId: string): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(login(teamId, loginId), { method: 'DELETE' })
}

/** 给设备上的这份连接起名；空字符串是去掉名字。用它加入项目的 Claude Code 随之改名。 */
export function renameModelConnection(teamId: number, loginId: string, name: string): Promise<ClaudeCodeLoginState> {
  return connectorRequest<ClaudeCodeLoginState>(login(teamId, loginId), {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

/** 保存到账号里：服务先被调用一次，通不过就不保存，错误里写着它说了什么。 */
export function addAccountConnection(teamId: number, input: AccountConnectionInput): Promise<AccountConnection> {
  const service = input.kind === 'model_service'
  return connectorRequest<AccountConnection>(root(teamId), {
    method: 'POST',
    body: JSON.stringify({
      kind: input.kind,
      allowed_models: input.allowedModels,
      key: input.key,
      name: input.name || null,
      base_url: service ? input.baseUrl : null,
      model: service ? input.model : null,
    }),
  })
}

/** 再调用一次服务，记下它这次怎么说。 */
export function checkAccountConnection(teamId: number, id: string): Promise<AccountConnection> {
  return connectorRequest<AccountConnection>(`${held(teamId, id)}/check`, { method: 'POST' })
}

export function renameAccountConnection(teamId: number, id: string, name: string): Promise<AccountConnection> {
  return connectorRequest<AccountConnection>(held(teamId, id), { method: 'PATCH', body: JSON.stringify({ name }) })
}

/** 解除：密钥立即从平台删除。 */
export function removeAccountConnection(teamId: number, id: string): Promise<AccountConnection> {
  return connectorRequest<AccountConnection>(held(teamId, id), { method: 'DELETE' })
}

export function setConnectionModels(teamId: number, id: string, models: string[], onDevice: boolean) {
  return connectorRequest(`${onDevice ? login(teamId, id) : held(teamId, id)}/models`, {
    method: 'PUT',
    body: JSON.stringify({ allowed_models: models }),
  })
}

export function startTeamClaudeLogin(teamId: number) {
  return connectorRequest<{ ticket: string; server: string; command: string }>(`/teams/${teamId}/claude-login`, {
    method: 'POST',
  })
}

export interface PersonalConnectionChoice {
  id: string
  name: string | null
  kind: string
  allowed_models: string[]
  added: boolean
}

export function personalConnectionChoices(teamId: number) {
  return connectorRequest<{ connections: PersonalConnectionChoice[] }>(`/teams/${teamId}/personal-model-resources`)
}

export function putPersonalResource(teamId: number, id: string) {
  return connectorRequest<ModelConnections>(`/teams/${teamId}/personal-model-resources/${encodeURIComponent(id)}`, {
    method: 'PUT',
  })
}

export function removePersonalResource(teamId: number, id: string) {
  return connectorRequest<ModelConnections>(`/teams/${teamId}/personal-model-resources/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}
