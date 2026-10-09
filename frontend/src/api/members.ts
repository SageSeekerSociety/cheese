import type { ListPayload, Project, ProjectInvitation, ProjectMemberRow, Topic, TopicMemberRow } from '../cx_types'

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

// 转让项目给名册上的另一个人。所有者自己退不掉（后端会拒，得先转让），这是他
// 离得开的那条路的第一步。新所有者必须已经在名册上（后端会验，不在就退回来一句
// 「请先把 TA 加进项目成员」）；他接手之后，原所有者就只剩成员身份，再退出一次
// 才真的走（membership/services.py 的 leave）。
export function setProjectOwner(projectId: string, ownerHandle: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}/owner`, {
    method: 'PUT',
    body: JSON.stringify({ owner_handle: ownerHandle }),
  })
}

// ---- 外部成员：只能点名邀请，本人接受才进来 ----------------------------------
// 团队里的人自动就在团队的每个项目里；项目自己能加的只有团队以外的人。进来之后他
// 看得见这个项目的话题，所以要由他本人点头——邀请发出去，他接受了才算数。

export function inviteExternalMember(projectId: string, handle: string): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/projects/${encodeURIComponent(projectId)}/invitations`, {
    method: 'POST',
    body: JSON.stringify({ user_handle: handle }),
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

export function respondToInvitation(invitationId: string, accept: boolean): Promise<ProjectInvitation> {
  return request<ProjectInvitation>(`/invitations/${encodeURIComponent(invitationId)}/respond`, {
    method: 'POST',
    body: JSON.stringify({ accept }),
  })
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
