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
