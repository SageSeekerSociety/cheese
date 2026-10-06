// 运行记录页（`/admin/run-records`）的 HTTP 层与形状。合并、计数、分段都在服务端
// （`app/domain/run_record/admin.py`）做完，这里只取。
import { request } from '@/api'

export type RunRecordWindow = '24h' | '7d' | '30d'
export type RunRecordGroupFilter = 'all' | 'errors' | 'recovered'

/** 一种事：报错按指纹合并，别的按那句话合并。 */
export interface RunRecordGroup {
  key: string
  kind: string
  severity: string
  title: string
  count: number
  projects: number
  first_at: string
  last_at: string
  /** 窗口切成 24 段，每段几次。 */
  buckets: number[]
}

export interface RunRecordOverview {
  window: RunRecordWindow
  since: string
  totals: { error_kinds: number; errors: number; recovered: number }
  groups: RunRecordGroup[]
}

export interface RunRecordPlace {
  project_id: string | null
  project: string | null
  conversation_id: string | null
  at: string
}

export interface RunRecordDetail {
  kind: string
  severity: string
  content: string
  meta: Record<string, unknown>
  at: string
  places: RunRecordPlace[]
  more_places: number
}

export function getRunRecords(
  window: RunRecordWindow,
  group: RunRecordGroupFilter,
  q: string
): Promise<RunRecordOverview> {
  const params = new URLSearchParams({ window, group })
  if (q) params.set('q', q)
  return request<RunRecordOverview>(`/admin/run-records?${params}`)
}

export function getRunRecordDetail(key: string, kind: string, window: RunRecordWindow): Promise<RunRecordDetail> {
  const params = new URLSearchParams({ key, kind, window })
  return request<RunRecordDetail>(`/admin/run-records/detail?${params}`)
}
