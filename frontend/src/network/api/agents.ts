// 「我的 Agent」页的连接器 API（owner 视角，与 project 无关）。
// 契约见 docs / api-contract：所有路由挂在 `/connector` 前缀下，鉴权用
// `Authorization: Bearer <AccountService.accessToken>`（沿用 GroupSidePanel 的 headers() 模式）。
// 连接器路由直接返回 JSON（不套 {code,message,data}）。
import { authFetch } from './connectorFetch'

/** 身份对象（人类 / agent 通用）。契约里的 Member / UserRef。 */
export interface Member {
  user_id: number
  nickname: string
  avatar_id: number | null
  is_agent: boolean
  role?: number | null
  agent_status?: string | null
  elapsed?: string | null
  tokens?: string | null
  sid?: string | null
  device_id?: string | null
}

/** 设备：一台接入的客户机，其上可驱动多个 agent。 */
export interface Device {
  device_id: string
  name: string
  online: boolean
  agents: Member[]
}

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  // Shared connector transport: refreshes an expired access token on 401 and retries,
  // so 我的 Agent keeps working on a long-open tab (see ./connectorFetch).
  const r = await authFetch(url, init ?? {}, true)
  const data = (await r.json().catch(() => ({}))) as T & { message?: string }
  if (!r.ok) {
    throw new Error(data?.message ?? `请求失败（${r.status}）`)
  }
  return data
}

/** 我的所有设备（含其上的 agent 成员）。 */
export const listMyDevices = () =>
  request<{ devices: Device[] }>('/connector/my/devices')

/** 在某设备上创建一个 agent；创建后按 nickname/avatar_id 更新其 profile。 */
export const createAgent = (body: {
  device_id: string
  nickname?: string
  avatar_id?: number
  copy_from_agent_user_id?: number
  // 「复制自」时可选的目标工作目录：不填则沿用源 agent 的 cwd。
  target_cwd?: string
  // 「接入已有会话」：resume 设备上已经存在的一个 Claude 会话，而不是新建或复制。
  // 两个字段必须同时提供；与 copy_from_agent_user_id 互斥。
  attach_session_id?: string
  attach_cwd?: string
}) =>
  request<{ agent: Member }>('/connector/my/agents', {
    method: 'POST',
    body: JSON.stringify(body),
  })

/** 改名 / 设头像。以 agent 的 user_id 定位。 */
export const updateAgent = (
  agentUserId: number,
  body: { nickname?: string; avatar_id?: number },
) =>
  request<{ agent: Member }>(`/connector/my/agents/${agentUserId}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })

/** 停止并软删 agent；以在线现场的 sid 定位。 */
export const deleteAgent = (sid: string) =>
  request<{ closed: boolean }>(`/connector/my/agents/${sid}`, {
    method: 'DELETE',
  })

/**
 * 重建 agent（复用原用户，身份与历史不变），以 user_id 定位。默认 resume=true 用原先的
 * Claude 会话恢复对话。现场仍存活时后端返回 412，UI 提示确认后带 force=true 再调一次。
 */
export const recreateAgent = (
  agentUserId: number,
  opts: { resume?: boolean; force?: boolean } = {},
) =>
  request<{ agent: Member; resumed: boolean }>(`/connector/my/agents/${agentUserId}/recreate`, {
    method: 'POST',
    body: JSON.stringify({ resume: opts.resume ?? true, force: opts.force ?? false }),
  })

/** 重命名设备。 */
export const renameDevice = (deviceId: string, name: string) =>
  request<{ device: Device }>(`/connector/my/devices/${deviceId}`, {
    method: 'PATCH',
    body: JSON.stringify({ name }),
  })

/** 删除设备。 */
export const deleteDevice = (deviceId: string) =>
  request<{ deleted: boolean }>(`/connector/my/devices/${deviceId}`, {
    method: 'DELETE',
  })
