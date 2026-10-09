import type { ListPayload, Project, ProjectSite, ProjectSiteInfo, WaitingItem } from '../cx_types'

import { ApiError, request } from './http'

export function listProjects(teamId?: number): Promise<ListPayload<Project>> {
  const q = teamId != null ? `?team_id=${teamId}` : ''
  return request<ListPayload<Project>>(`/projects${q}`)
}

/** 待我处理：跨项目、点到我的那些事项，最近动过的在前。
 *
 *  和看板读同一份规则（后端 `room_task/presentation.py`），所以一件事在看板上是待
 *  处理，在这里就是待处理。它不是通知列表的另一种视图：通知是事件记录，答不出
 *  「现在还没处理完的有哪些」。 */
export function listAwaitingMe(): Promise<ListPayload<WaitingItem>> {
  return request<ListPayload<WaitingItem>>('/awaiting-me')
}

export function createProject(
  name: string,
  teamId?: number,
  externalTaskId?: number,
  forgeKind?: 'forgejo' | 'github_app',
  intent?: string,
  agentName?: string,
  id?: string
): Promise<Project> {
  return request<Project>('/projects', {
    method: 'POST',
    body: JSON.stringify({
      id,
      name,
      team_id: teamId,
      // Set when the project is created FROM a 赛题, so the 赛题 can find it
      // again. Absent for a project made from the rail.
      external_task_id: externalTaskId,
      forge_kind: forgeKind,
      // 建项目时问的那一句「你打算做什么」。空串就是没答，服务端不写任何东西。
      intent,
      agent_name: agentName,
    }),
  })
}

// The 2.0 projects created from one 赛题 — what the 赛题 page shows instead of
// blindly offering to create another.
export function listProjectsForTask(taskId: number): Promise<ListPayload<Project>> {
  return request<ListPayload<Project>>(`/projects/by-task/${taskId}`)
}

// Single project card.
export function getProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}`)
}

/** 归档项目：只有所有者能做。项目从所有人的列表里消失、不能再修改，里面的内容都保留。 */
export function archiveProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}/archive`, { method: 'POST' })
}

/** 取消归档：项目和随它一起归档的话题回来。 */
export function unarchiveProject(projectId: string): Promise<Project> {
  return request<Project>(`/projects/${encodeURIComponent(projectId)}/unarchive`, { method: 'POST' })
}

/** 我归档过的项目 —— 它们只在这里列出来。 */
export function listArchivedProjects(): Promise<ListPayload<Project>> {
  return request<ListPayload<Project>>('/projects?archived=true')
}

/** 后端拒绝写入一个已归档项目时，错误名是这个。 */
export function isProjectArchivedError(e: unknown): boolean {
  return e instanceof ApiError && e.code === 'ProjectArchivedError'
}

export function getProjectSite(projectId: string): Promise<ProjectSiteInfo> {
  return request<ProjectSiteInfo>(`/projects/${encodeURIComponent(projectId)}/site`)
}

export function publishProjectSite(
  projectId: string,
  body: { directory: string; expected_source_revision: string }
): Promise<ProjectSite> {
  return request<ProjectSite>(`/projects/${encodeURIComponent(projectId)}/site`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function requestSiteSession(projectId: string): Promise<{ url: string; grant: string }> {
  return request<{ url: string; grant: string }>(`/projects/${encodeURIComponent(projectId)}/site-session`, {
    method: 'POST',
  })
}

/**
 * 「开始清单」里要问服务端的那一条：我在这个项目里跟 AI 队友说上过话没有——在哪段
 * 对话里都算，任务里的对话也算（频道那一栏读不到那里）。
 */
export function getGettingStarted(projectId: string): Promise<{ talked: boolean }> {
  return request<{ talked: boolean }>(`/projects/${encodeURIComponent(projectId)}/getting-started`)
}
