// 技能：项目存下来的做法，确认过的那一版带进这个项目之后的每个会话。
//
// 从 `api.ts` 拆出来：那个文件在上限之上，只能变短；技能自己是一个整体（页面和
// 房间里那张提议卡都读它）。
import type { ListPayload } from '../cx_types'
import type { ProjectSkill, ProjectSkillContent, ProjectSkillDetail, SkillImportPreview } from '../lib/projectSkill'

import { request } from './http'

export type {
  ProjectSkill,
  ProjectSkillContent,
  ProjectSkillDetail,
  ProjectSkillRevision,
  SkillFileEntry,
  SkillImportPreview,
  SkillOrigin,
  SkillProposal,
} from '../lib/projectSkill'

export function listProjectSkills(projectId: string): Promise<ListPayload<ProjectSkill>> {
  return request<ListPayload<ProjectSkill>>(`/projects/${encodeURIComponent(projectId)}/skills`)
}

/** 一份技能，连同配套文件的内容和历史版本。 */
export function getProjectSkill(id: string): Promise<ProjectSkillDetail> {
  return request<ProjectSkillDetail>(`/skills/${encodeURIComponent(id)}`)
}

/** 人在技能页上新建一份；`imported` 是从导入预览添加的，只有项目管理员能加。 */
export function addProjectSkill(
  projectId: string,
  body: ProjectSkillContent & { name: string; imported?: boolean }
): Promise<ProjectSkill> {
  return request<ProjectSkill>(`/projects/${encodeURIComponent(projectId)}/skills`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** 读一份别处做的技能（上传的 SKILL.md、压缩包，或 GitHub 地址），只读不存。 */
export async function previewSkillImport(
  projectId: string,
  source: { file: File } | { url: string }
): Promise<SkillImportPreview> {
  const body =
    'file' in source ? { filename: source.file.name, content: await base64Of(source.file) } : { url: source.url }
  return request<SkillImportPreview>(`/projects/${encodeURIComponent(projectId)}/skills/import-preview`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

async function base64Of(file: File): Promise<string> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  let binary = ''
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000))
  return btoa(binary)
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

// ---- 技能面板：一个队友带哪些技能，和它们现在能不能跑 ----
//
// 平台随会话发货的原生技能目录（`/skill-catalog`），以及每一颗探一次的健康。两条都
// 按项目授权（后端 `_in_project`）。队友带到哪些技能存在 `agent.configuration.skills`，
// 那条走 `updateProjectAgent`，不在这里。
export interface SkillHealth {
  status: 'ok' | 'degraded' | 'unavailable'
  detail: string
}

export function listSkillCatalog(projectId: string): Promise<ListPayload<{ name: string; description: string }>> {
  return request<ListPayload<{ name: string; description: string }>>(
    `/projects/${encodeURIComponent(projectId)}/skill-catalog`
  )
}

/** 同一份发货目录，不经项目：出题人给题目挑技能时还没有项目（`GET /skill-catalog`）。 */
export function listPlatformSkills(): Promise<ListPayload<{ name: string; description: string }>> {
  return request<ListPayload<{ name: string; description: string }>>('/skill-catalog')
}

export function getSkillHealth(projectId: string, name: string): Promise<SkillHealth> {
  const path = `/projects/${encodeURIComponent(projectId)}/skills/${encodeURIComponent(name)}/health`
  return request<SkillHealth>(path)
}
