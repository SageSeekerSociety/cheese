// 「模型连接」（#2991）：成员自己的 Claude Code 在各台设备上登录的账号或模型服务，
// 以及保存在账号里的 API key 和模型服务，形状由后端定（`app/api/routes/connector.py`
// 的 ModelConnectionsOut）。
import type { components } from '../api/openapi'

type Schemas = components['schemas']

export type ModelConnection = Schemas['ModelConnectionOut']
export type ClaudeCodeLoginState = Schemas['ClaudeCodeLoginOut']
export type ModelConnections = Schemas['ModelConnectionsOut']
/** 保存在账号里的一份连接：Anthropic API key 或自定义模型服务。只有密钥后四位，没有密钥。 */
export type AccountConnection = Schemas['AccountConnectionOut']

/** 「使用自定义模型服务」可以选的设备：自己接入、此刻在线的那几台。 */
export interface ModelServiceDevice {
  device_id: string
  name: string
}

/** 这份连接是哪一种：Claude 订阅、Anthropic API key，还是自定义模型服务。 */
export type ModelConnectionKind = 'subscription' | 'apiKey' | 'modelService'

export function connectionKind(connection: ModelConnection): ModelConnectionKind {
  if (connection.auth_method === 'model_service') return 'modelService'
  return connection.subscription_type ? 'subscription' : 'apiKey'
}

/** 账号里能保存的两种连接。 */
export type AccountConnectionKind = 'api_key' | 'model_service'

/** 在「添加 API key」「添加模型服务」里填的东西；API key 没有服务地址和模型。 */
export interface AccountConnectionInput {
  kind: AccountConnectionKind
  key: string
  name: string
  baseUrl: string
  model: string
}
