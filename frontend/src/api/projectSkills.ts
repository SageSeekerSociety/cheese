// 工作方法：项目存下来的做法，确认过的那一版带进这个项目之后的每个会话。
//
// 从 `api.ts` 拆出来：那个文件在上限之上，只能变短；工作方法自己是一个整体（页面和
// 房间里那张提议卡都读它）。
import type { ListPayload } from '../cx_types'
import type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision } from '../lib/projectSkill'

import { request } from './http'

export type { ProjectSkill, ProjectSkillContent, ProjectSkillRevision, SkillProposal } from '../lib/projectSkill'

export function listProjectSkills(projectId: string): Promise<ListPayload<ProjectSkill>> {
  return request<ListPayload<ProjectSkill>>(`/projects/${encodeURIComponent(projectId)}/skills`)
}

export function getProjectSkill(id: string): Promise<ProjectSkill & { revisions: ProjectSkillRevision[] }> {
  return request<ProjectSkill & { revisions: ProjectSkillRevision[] }>(`/skills/${encodeURIComponent(id)}`)
}

export function createProjectSkill(
  topicId: string,
  body: ProjectSkillContent & { name: string }
): Promise<ProjectSkill> {
  return request<ProjectSkill>(`/topics/${encodeURIComponent(topicId)}/skills`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateProjectSkill(id: string, body: Partial<ProjectSkillContent>): Promise<ProjectSkill> {
  return request<ProjectSkill>(`/skills/${encodeURIComponent(id)}`, { method: 'PATCH', body: JSON.stringify(body) })
}

export function confirmProjectSkill(id: string): Promise<ProjectSkill> {
  return request<ProjectSkill>(`/skills/${encodeURIComponent(id)}/confirm`, { method: 'POST' })
}

export function restoreProjectSkill(id: string, revision: number): Promise<ProjectSkill> {
  return request<ProjectSkill>(`/skills/${encodeURIComponent(id)}/revisions/${revision}/restore`, { method: 'POST' })
}

/** 拒绝芝士提议的：新的一份不再被提议，对已保存那份的改动退回保存的版本。 */
export function declineProjectSkill(id: string): Promise<ProjectSkill> {
  return request<ProjectSkill>(`/skills/${encodeURIComponent(id)}/decline`, { method: 'POST' })
}

export function deleteProjectSkill(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/skills/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
