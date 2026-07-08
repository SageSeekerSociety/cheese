// 项目成员管理的连接器 API（知是 2.0 工作区）。忽略角色/权限差异：任何项目成员都拥有
// 全部权限，因此列出 / 搜索候选 / 添加 / 移除成员都只要求调用者是本项目成员。
// 契约：所有路由挂在 `/connector` 前缀下，直接返回 JSON（不套 {code,message,data}），
// 鉴权用 `Authorization: Bearer <AccountService.accessToken>`（沿用 connectorFetch）。
import type { Member } from './agents'
import { connectorGet, connectorSend } from './connectorFetch'

export type { Member }

/** 某项目的成员列表（含每个成员是否为在线 agent 及其现场 device/sid）。 */
export const listProjectMembers = (projectId: number) =>
  connectorGet<{ members: Member[]; total: number }>(`/connector/projects/${projectId}/members`)

/** 搜索可加入项目的用户（按昵称或用户名模糊匹配，排除已是成员者）。空查询返回空。 */
export const searchMemberCandidates = (projectId: number, q: string) =>
  connectorGet<{ candidates: Member[] }>(
    `/connector/projects/${projectId}/member-candidates?q=${encodeURIComponent(q)}`,
  )

/** 添加成员（扁平：一律以普通成员身份加入）。 */
export const addProjectMember = (projectId: number, userId: number) =>
  connectorSend<{ member: Member }>('POST', `/connector/projects/${projectId}/members`, {
    user_id: userId,
  })

/** 移除成员。 */
export const removeProjectMember = (projectId: number, userId: number) =>
  connectorSend<{ ok: boolean }>('DELETE', `/connector/projects/${projectId}/members/${userId}`)

/** 在项目里创建一个 agent（后端自动挑一台接入本项目且在线的客户机）。 */
export const createProjectAgent = (projectId: number) =>
  connectorSend<{ agent_user_id: number; agent_username: string; sid: string }>(
    'POST',
    `/connector/projects/${projectId}/agents`,
    {},
  )

/** 停止并软删一个在项目里运行的 agent（以其在线现场的 device_id + sid 定位）。 */
export const deleteProjectAgent = (deviceId: string, sid: string) =>
  connectorSend<{ ok: boolean; agent_user_id: number }>(
    'DELETE',
    `/connector/devices/${deviceId}/agents/${sid}`,
  )

/**
 * 重建一个 agent（复用原用户，身份与历史不变）。后端自动挑一台接入本项目的在线客户机。
 * - resume=false：全新会话（手动重建）。
 * - resume=true：用原先的 Claude session 恢复对话（例如客户机重启后把 agent 拉回来）。
 * - force=false 且现场仍存活时后端返回 412，UI 应提示用户确认后带 force=true 再调一次。
 */
export const recreateProjectAgent = (
  projectId: number,
  agentUserId: number,
  opts: { resume?: boolean; force?: boolean } = {},
) =>
  connectorSend<{ agent_user_id: number; agent_username: string; sid: string; resumed: boolean }>(
    'POST',
    `/connector/projects/${projectId}/agents/${agentUserId}/recreate`,
    { resume: opts.resume ?? false, force: opts.force ?? false },
  )
