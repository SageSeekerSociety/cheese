import type { ListPayload } from '../cx_types'

import { request } from './http'

// ---- 记忆 (spec §8.4: 记忆可见): 一条记忆一个 markdown 文件 ----
export interface MemoryEntryOut {
  id: string
  scope: string
  content: string
  created_at: string
  updated_at: string
}
export function listMemory(projectId: string, userHandle?: string): Promise<ListPayload<MemoryEntryOut>> {
  const u = userHandle ? `&user_handle=${encodeURIComponent(userHandle)}` : ''
  return request<ListPayload<MemoryEntryOut>>(`/memory?project_id=${encodeURIComponent(projectId)}${u}`)
}
export function deleteMemory(entryId: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/memory/${encodeURIComponent(entryId)}`, {
    method: 'DELETE',
  })
}

// ---- 记忆的改动记录：最近改动，以及从一条改动恢复 ----
/** 一次改动走的是哪条路（`backend/app/domain/memory/writers.py`）。 */
export type MemoryWriter = 'turn' | 'dream_team' | 'dream_private' | 'panel' | 'restore' | 'skill' | 'migration'
export interface MemoryChangeOut {
  id: string
  scope: 'team' | 'private'
  owner_handle: string
  path: string
  writer: MemoryWriter
  changed_by: string
  created_at: string
  /** 这一次是新建：改之前什么都没有，所以没有可恢复的。 */
  created: boolean
  deleted: boolean
  /** 改之前 → 改之后的统一 diff。 */
  diff: string
}
/** 一页改动，新的在前；`next` 是下一页的 `before`，读到头了为空。 */
export interface MemoryChangesPage {
  data: MemoryChangeOut[]
  next: string | null
}
export function listMemoryChanges(
  projectId: string,
  scope: 'team' | 'private',
  ownerHandle?: string,
  before?: string
): Promise<MemoryChangesPage> {
  const owner = ownerHandle ? `&owner_handle=${encodeURIComponent(ownerHandle)}` : ''
  const page = before ? `&before=${encodeURIComponent(before)}` : ''
  return request<MemoryChangesPage>(
    `/memory/files/history?project_id=${encodeURIComponent(projectId)}&scope=${scope}${owner}${page}`
  )
}
export function restoreMemoryChange(
  projectId: string,
  changeId: string
): Promise<{ file: { path: string; version: number } }> {
  return request<{ file: { path: string; version: number } }>(
    `/memory/files/projects/${encodeURIComponent(projectId)}/history/${encodeURIComponent(changeId)}/restore`,
    { method: 'POST' }
  )
}
