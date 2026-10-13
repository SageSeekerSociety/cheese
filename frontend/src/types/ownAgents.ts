// 成员自己的 Claude Code（#2991）在前端用到的形状。
import type { MyDevice } from '@/cx_types'
import type { components } from '../api/openapi'

type Schemas = components['schemas']

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

/** 项目里成员自己的 Claude Code：项目允不允许接入、允许哪几类连接，以及谁加入了、
 *  各用哪份模型连接。一个人每份连接可以加入一位；设备名和在线状态只在读者自己的那几行有
 *  （`machine`，别人的是 null）。读者自己还能加入的连接在 `my_connections`。
 *  形状由后端定（`app/api/routes/projects_own_agents.py` 的 OwnAgentsOut）。 */
export type OwnAgentsSettings = Schemas['OwnAgentsOut']
export type OwnAgentRow = Schemas['OwnAgentOut']
export type OwnAgentConnection = Schemas['OwnAgentConnectionOut']
/** 读者自己的一份模型连接，可以加入这个项目；`id` 是存放它的那台设备。 */
export type MyOwnAgentConnection = Schemas['MyConnectionOut']
/** 连接的类型：Claude 订阅、Anthropic API key、自定义模型服务。 */
export type OwnAgentKind = Schemas['ConnectionKind']
/** 谁可以叫它：`owner` 只有主人，`project` 项目里的人都能叫。只有主人能改。 */
export type OwnAgentCallers = Schemas['OwnCallers']

/** 设置里列出各类连接的先后。 */
export const OWN_AGENT_KINDS: readonly OwnAgentKind[] = ['subscription', 'api_key', 'model_service']

/** 「我的设备」里这台机器上机主的 Claude Code 登录状态；null 是还没被问过。 */
export function claudeLoginOf(device: MyDevice): ClaudeCodeLogin | null {
  return (device as MyDevice & { claude_code?: ClaudeCodeLogin | null }).claude_code ?? null
}
