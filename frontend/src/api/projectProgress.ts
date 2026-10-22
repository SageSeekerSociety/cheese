// 项目总览的「最近进展」：最近两周这个项目里发生了什么，新的在前。
import type { ListPayload } from '../cx_types'
import type { ProgressItem } from '../types/projectProgress'

import { request } from './http'

export function listProjectProgress(projectId: string): Promise<ListPayload<ProgressItem>> {
  return request<ListPayload<ProgressItem>>(`/projects/${encodeURIComponent(projectId)}/progress`)
}

/** 现状里一块的一条出处：一次被采纳的交付、一个开着的任务，或项目目标里的一句原话。 */
export interface SituationSource {
  kind: 'delivery' | 'task' | 'goal'
  card_id: string | null
  task_id: string | null
  room_id: string | null
  /** 交付的改动标题或任务标题；`goal` 是空串。 */
  title: string
  /** `goal` 引的那句原话；别的是空串。 */
  quote: string
}

/** 现状里的一块。状态由后端按它引的记录定，不是模型说的。 */
export interface SituationPart {
  name: string
  text: string
  status: 'usable' | 'underway' | 'not_started'
  sources: SituationSource[]
}

/** 项目总览最上面的「现状」：上一次整理出的那几块。 */
export interface ProjectSituation {
  /** 还没写成过、或者近两周没有可写的事，是空的。 */
  parts: SituationPart[]
  written_at: string | null
  /** 后台正在重写：写完会推一帧 `situation`。 */
  writing: boolean
}

export function getProjectSituation(projectId: string): Promise<ProjectSituation> {
  return request<ProjectSituation>(`/projects/${encodeURIComponent(projectId)}/situation`)
}
