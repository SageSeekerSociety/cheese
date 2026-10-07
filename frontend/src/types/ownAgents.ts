// 成员自己的 Claude Code（#2991）在前端用到的形状。
import type { MyDevice } from '@/cx_types'

/** 机主自己的 Claude Code 在一台机器上有没有为平台登录，只给机主看。 */
export interface ClaudeCodeLogin {
  installed: boolean
  logged_in: boolean
  /** `model_service` when the owner pointed it at another model service. */
  auth_method: string | null
  subscription_type: string | null
  /** The model a model service is called with. */
  model: string | null
  checked_at: string
}

/** What a model service is set up with, as the owner fills it in on this device. */
export interface ModelServiceInput {
  baseUrl: string
  token: string
  model: string
}

/** The model a model service is called with, when the machine's Claude Code uses one;
 *  null when it uses a Claude account or is not logged in. */
export function modelServiceOf(login: ClaudeCodeLogin | null): string | null {
  return login?.logged_in && login.auth_method === 'model_service' ? login.model ?? '' : null
}

/** 项目里成员自己的 Claude Code：项目允不允许接入，以及谁接了、在哪几台电脑上。 */
export interface OwnAgentsSettings {
  allowed: boolean
  can_manage: boolean
  agents: OwnAgentRow[]
}

export interface OwnAgentRow {
  handle: string
  name: string
  owner_handle: string | null
  owner_name: string | null
  machines: { name: string; online: boolean }[]
}

/** 「我的设备」里这台机器上机主的 Claude Code 登录状态；null 是还没被问过。 */
export function claudeLoginOf(device: MyDevice): ClaudeCodeLogin | null {
  return (device as MyDevice & { claude_code?: ClaudeCodeLogin | null }).claude_code ?? null
}
