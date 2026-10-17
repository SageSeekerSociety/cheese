// 项目总览的「最近进展」：最近两周这个项目里发生了什么，新的在前。
import type { ListPayload } from '../cx_types'
import type { ProgressItem } from '../types/projectProgress'

import { request } from './http'

export function listProjectProgress(projectId: string): Promise<ListPayload<ProgressItem>> {
  return request<ListPayload<ProgressItem>>(`/projects/${encodeURIComponent(projectId)}/progress`)
}

/** 项目总览最上面的「现状」：芝士上一次写下的那几句。 */
export interface ProjectSituation {
  /** Markdown；还没写成过、或者近两周没有可写的事，是空串。 */
  content: string
  written_at: string | null
  /** 后台正在重写：写完会推一帧 `situation`。 */
  writing: boolean
}

export function getProjectSituation(projectId: string): Promise<ProjectSituation> {
  return request<ProjectSituation>(`/projects/${encodeURIComponent(projectId)}/situation`)
}
