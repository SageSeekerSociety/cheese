import type { ListPayload, ProjectInvitation, ProjectMemberRow, Topic, TopicMemberRow } from '../cx_types'

import { changesAwaiting } from './awaitingChanged'
import { request } from './http'

// 项目成员列表 (used by the 改验收人 menu). Returns {data:[{user_handle, role}]}.
export function listProjectMembers(projectId: string): Promise<ListPayload<ProjectMemberRow>> {
  return request<ListPayload<ProjectMemberRow>>(`/projects/${encodeURIComponent(projectId)}/members`)
}

// 把一位外部成员移出项目。只有外部成员能这样移出——团队成员的去留在团队里定。后端
// 在服务层判「谁能移」，并且**不认**请求体里自称的 handle：身份从 token 解析。
export function removeProjectMember(projectId: string, handle: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(handle)}`,
    { method: 'DELETE' }
  )
}

// 自己退出项目。路径是 `/membership` 而不是 `/members/me`：`/members/{handle}` 那条
// 路由先注册，`me` 到了那里就是一个人的名字。同样不传 handle —— 退的恒是当前身份
// 那个人，后端没有代退的入口（membership/services.py 的 `leave`）。
export function leaveProject(projectId: string): Promise<{ deleted: boolean }> {
  return request<{ deleted: boolean }>(`/projects/${encodeURIComponent(projectId)}/membership`, {
    method: 'DELETE',
  })
}

// ---- 角色与转让（#3830、#4795）-------------------------------------------------

/** 项目里的一个角色。`key` 是 `admin`、`member` 或项目自己那个角色的编号。 */
export interface ProjectRoleRow {
  key: string
  /** 项目给它起的名字；内置角色和没改过名的模板角色为空，由界面按 builtin / template 写。 */
  name: string | null
  builtin: 'admin' | 'member' | null
  template: 'mentor' | null
  permissions: string[]
  /** 内置角色的默认权限，用来恢复默认；别的角色为空。 */
  defaults: string[] | null
  holders: number
}

// 给比自己低的人换一个角色：`admin`、`member`、项目自己的角色，或 `mentor`（项目的导师角色）。
export function setMemberRole(projectId: string, handle: string, role: string): Promise<{ role: string }> {
  return request<{ role: string }>(
    `/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(handle)}/role`,
    { method: 'PUT', body: JSON.stringify({ role }) }
  )
}

export function listProjectRoles(projectId: string): Promise<{ roles: ProjectRoleRow[] }> {
  return request<{ roles: ProjectRoleRow[] }>(`/projects/${encodeURIComponent(projectId)}/roles`)
}

/** 新增一个角色：权限从另一个角色复制（`copy_from`），或从模板来（`template`，可以不起名）。 */
export function createProjectRole(
  projectId: string,
  body: { name: string | null; copy_from?: string; template?: 'mentor' }
): Promise<ProjectRoleRow> {
  return request<ProjectRoleRow>(`/projects/${encodeURIComponent(projectId)}/roles`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateProjectRole(
  projectId: string,
  key: string,
  body: { name?: string; permissions?: string[] }
): Promise<ProjectRoleRow> {
  return request<ProjectRoleRow>(`/projects/${encodeURIComponent(projectId)}/roles/${encodeURIComponent(key)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

/** 删除项目自己的一个角色；还有人在用时先把他们改为 `moveTo`。 */
export function deleteProjectRole(projectId: string, key: string, moveTo?: string): Promise<{ deleted: boolean }> {
  const query = moveTo ? `?move_to=${encodeURIComponent(moveTo)}` : ''
  return request<{ deleted: boolean }>(
    `/projects/${encodeURIComponent(projectId)}/roles/${encodeURIComponent(key)}${query}`,
    { method: 'DELETE' }
  )
}

export interface ProjectTransfer {
  id: string
  project_id: string
  from_handle: string
  to_handle: string
  status: string
}

// 所有者把项目交给项目里的一个人；对方接受之前什么都不变。
export function offerTransfer(projectId: string, toHandle: string): Promise<ProjectTransfer> {
  return request<ProjectTransfer>(`/projects/${encodeURIComponent(projectId)}/transfer`, {
    method: 'POST',
    body: JSON.stringify({ to_handle: toHandle }),
  })
}

export function cancelTransfer(projectId: string): Promise<ProjectTransfer> {
  return request<ProjectTransfer>(`/projects/${encodeURIComponent(projectId)}/transfer`, {
    method: 'DELETE',
  })
}

// 答了这次转让，它就从「等你处理」里了结，所以告诉角标重读。
export function respondToTransfer(transferId: string, accept: boolean): Promise<ProjectTransfer> {
  return changesAwaiting(
    request<ProjectTransfer>(`/transfers/${encodeURIComponent(transferId)}/respond`, {
      method: 'POST',
      body: JSON.stringify({ accept }),
    })
  )
}

// 把自己的个人项目移到自己管理的团队。
export function moveProjectToTeam(projectId: string, teamId: number): Promise<{ project_id: string; team_id: number }> {
  return request<{ project_id: string; team_id: number }>(`/projects/${encodeURIComponent(projectId)}/move`, {
    method: 'POST',
    body: JSON.stringify({ team_id: teamId }),
  })
}

// ---- 外部成员：只能点名邀请，本人接受才进来 ----------------------------------
// 团队里的人自动就在团队的每个项目里；项目自己能加的只有团队以外的人。进来之后他
// 看得见这个项目的话题，所以要由他本人点头——邀请发出去，他接受了才算数。

// `role` 是一个角色的 key，或 `mentor`：项目还没有导师角色时，后端照模板建出来。
export function inviteExternalMember(projectId: string, handle: string, role = 'member'): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/projects/${encodeURIComponent(projectId)}/invitations`, {
    method: 'POST',
    body: JSON.stringify({ user_handle: handle, role }),
  })
}

export interface LookedUpUser {
  id: number // what a team invitation names the person by
  handle: string
  name: string
  avatar_id: number | null
}

// 按完整的用户名或邮箱**精确**找一个人，不做模糊搜索（为什么见 useAccountLookup）。找不到是 404。
export function lookupUser(q: string): Promise<LookedUpUser> {
  return request<LookedUpUser>(`/users/lookup?q=${encodeURIComponent(q)}`)
}

export function listProjectInvitations(projectId: string): Promise<ListPayload<ProjectInvitation>> {
  return request<ListPayload<ProjectInvitation>>(`/projects/${encodeURIComponent(projectId)}/invitations`)
}

// 等我答复的邀请。没有 project 那一层是刻意的：被邀请的人还不在那个项目里，一个
// 项目作用域的接口他根本够不着。
export function listMyInvitations(): Promise<ListPayload<ProjectInvitation>> {
  return request<ListPayload<ProjectInvitation>>('/me/invitations')
}

// 答了这条邀请，它就从「等你处理」里了结，所以告诉角标重读。
export function respondToInvitation(invitationId: string, accept: boolean): Promise<ProjectInvitation> {
  return changesAwaiting(
    request<ProjectInvitation>(`/invitations/${encodeURIComponent(invitationId)}/respond`, {
      method: 'POST',
      body: JSON.stringify({ accept }),
    })
  )
}

export function revokeInvitation(invitationId: string): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/invitations/${encodeURIComponent(invitationId)}`, {
    method: 'DELETE',
  })
}

// ---- 话题成员名册 (群聊房间的地基, fusion-design §3) --------------------------
// Roster of a topic's group room. `actor` is the acting user's handle — no auth
// layer yet (agent-as-user is P1), so the backend authorizes mutations against
// the actor's topic role (owner/admin may manage the roster).

export function listTopicMembers(topicId: string): Promise<ListPayload<TopicMemberRow>> {
  return request<ListPayload<TopicMemberRow>>(`/topics/${encodeURIComponent(topicId)}/members`)
}

// 一个房间。任务的 id 问这条接口是 404，任务走 `api/tasks.ts`
// 的 `getTask`（`/topics/{task}/task`）。
export function getTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}`)
}
