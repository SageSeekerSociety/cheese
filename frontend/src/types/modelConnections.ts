// Computer-held Claude subscriptions and platform-held encrypted keys/services.
// The API's ModelConnectionsOut defines their wire shapes.
import type { components } from '../api/openapi'

type Schemas = components['schemas']

export type ModelConnection = Schemas['ModelConnectionOut']
export type ClaudeCodeLoginState = Schemas['ClaudeCodeLoginOut']
export type ModelConnections = Schemas['ModelConnectionsOut']
/** 保存在团队里的一份连接：Anthropic API key 或自定义模型服务。只有密钥后四位，没有密钥。 */
export type AccountConnection = Schemas['AccountConnectionOut']

/** 团队能保存的两种连接。 */
export type AccountConnectionKind = 'api_key' | 'model_service'

/** 在「添加 API key」「添加模型服务」里填的东西；API key 没有服务地址和模型。 */
export interface AccountConnectionInput {
  kind: AccountConnectionKind
  key: string
  name: string
  baseUrl: string
  model: string
  allowedModels: string[]
}
