// 项目总览的「最近进展」：最近两周这个项目里发生了什么，新的在前。
import type { ListPayload } from '../cx_types'
import type { ProgressItem } from '../types/projectProgress'

import { request } from './http'

export function listProjectProgress(projectId: string): Promise<ListPayload<ProgressItem>> {
  return request<ListPayload<ProgressItem>>(`/projects/${encodeURIComponent(projectId)}/progress`)
}
