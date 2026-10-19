// 「模型连接」（#2991）：列出成员自己的 Claude Code 在各台设备上的登录（一台设备可以有好几份，
// 每份各自一个 id），从浏览器重新检查、改名、解除其中一份，或在一台在线的设备上添一份用自定义
// 模型服务的登录（密钥交给设备，平台不保存）；以及保存在账号里的
// API key 和模型服务（密钥加密保存在平台上）：添加、测试、改名、解除。
import type {
  AccountConnection,
  AccountConnectionInput,
  ClaudeCodeLoginState,
  ModelConnections,
} from '../types/modelConnections'
import type { ModelServiceInput } from '../types/ownAgents'

import { connectorRequest } from './connector'

const ACCOUNT = '/my/model-connections'

function login(loginId: string): string {
  return `/my/claude-logins/${encodeURIComponent(loginId)}`
}

function held(id: string): string {
  return `${ACCOUNT}/${encodeURIComponent(id)}`
}

export function listModelConnections(): Promise<ModelConnections> {
  return connectorRequest<ModelConnections>(ACCOUNT)
}

/** 再问一次这份登录所在的设备；设备一次答出它上面的每一份。 */
export function checkModelConnection(loginId: string): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(`${login(loginId)}/check`, { method: 'POST' })
}

/** 解除：设备在线时它当场退出这份登录，不在线时等它下次连上；从这一刻起不再用它开会话。同一台设备上的其他登录不受影响。 */
export function revokeModelConnection(loginId: string): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(login(loginId), { method: 'DELETE' })
}

/** 给设备上的这份连接起名；空字符串是去掉名字。用它加入项目的 Claude Code 随之改名。 */
export function renameModelConnection(loginId: string, name: string): Promise<ClaudeCodeLoginState> {
  return connectorRequest<ClaudeCodeLoginState>(login(loginId), {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })
}

/** 在设备上添一份用这个模型服务的登录，答的就是这一份。 */
export function setModelService(deviceId: string, service: ModelServiceInput): Promise<ClaudeCodeLoginState | null> {
  return connectorRequest<ClaudeCodeLoginState | null>(
    `/my/devices/${encodeURIComponent(deviceId)}/claude-code/model-service`,
    {
      method: 'PUT',
      body: JSON.stringify({ base_url: service.baseUrl, token: service.token, model: service.model }),
    }
  )
}

/** 保存到账号里：服务先被调用一次，通不过就不保存，错误里写着它说了什么。 */
export function addAccountConnection(input: AccountConnectionInput): Promise<AccountConnection> {
  const service = input.kind === 'model_service'
  return connectorRequest<AccountConnection>(ACCOUNT, {
    method: 'POST',
    body: JSON.stringify({
      kind: input.kind,
      key: input.key,
      name: input.name || null,
      base_url: service ? input.baseUrl : null,
      model: service ? input.model : null,
    }),
  })
}

/** 再调用一次服务，记下它这次怎么说。 */
export function checkAccountConnection(id: string): Promise<AccountConnection> {
  return connectorRequest<AccountConnection>(`${held(id)}/check`, { method: 'POST' })
}

export function renameAccountConnection(id: string, name: string): Promise<AccountConnection> {
  return connectorRequest<AccountConnection>(held(id), { method: 'PATCH', body: JSON.stringify({ name }) })
}

/** 解除：密钥立即从平台删除。 */
export function removeAccountConnection(id: string): Promise<AccountConnection> {
  return connectorRequest<AccountConnection>(held(id), { method: 'DELETE' })
}
