// 例行事务：房间里的 AI 队友按时间、或在项目里发生某件事时自己开工的那些规则。
//
// 这一块从 `api.ts` 拆出来。那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；而规则本来就有它自己的边界 —— 一条规则属于一个房间、读的人要在这个房间的
// 名册上（`GET /projects/{id}/routines?topic=`），看得见的人就能改、能暂停、能立即执行
// 一次，只有**确认启用**和**删除**限规则主人与项目管理员（每一个回答里带回
// `can_manage`）—— 所以拆在这里是一个有理由的整体。
//
// 形状（`Routine` / `RoutineRun` / 那几张措辞表）在 `lib/routine.ts`：组件要吃那个形状
// 又碰不得 API 层，取数这一半只往上面接。
import type { ListPayload } from '../cx_types'
import type { Routine, RoutineInput, RoutineRun } from '../lib/routine'

import { request } from '../api'

export type { Routine, RoutineInput, RoutineRun } from '../lib/routine'

/**
 * 一个项目里的规则。
 *
 * `topicId` 是「只看这一个房间」：房间右侧那一格用它，后端凭这次请求的身份确认这个
 * 房间你看不看得到。不带就是整个项目的总览。
 */
export function listProjectRoutines(projectId: string, topicId?: string | null): Promise<ListPayload<Routine>> {
  const q = topicId ? `?topic=${encodeURIComponent(topicId)}` : ''
  return request<ListPayload<Routine>>(`/projects/${encodeURIComponent(projectId)}/routines${q}`)
}

/** 一条规则，外加它最近的执行记录。 */
export function getRoutine(id: string): Promise<Routine & { runs: RoutineRun[] }> {
  return request<Routine & { runs: RoutineRun[] }>(`/routines/${encodeURIComponent(id)}`)
}

/** 执行记录那一页问后端要的几样：翻到第几页、一页几行、怎么排、只看哪一段。 */
export interface RoutineRunsQuery {
  pageNo?: number
  perPage?: number
  /** 最新在前（默认）还是最早在前。 */
  order?: 'newest' | 'oldest'
  /** 只看没能执行的。 */
  failed?: boolean
  /** 最近几天；不给就是全部。 */
  days?: number | null
}

/** 一页执行记录。`page` 是后端确认过的页码，`total` 是这个筛选下共几次。 */
export type RoutineRunsPage = ListPayload<RoutineRun> & { page: number }

/**
 * 一条规则的执行记录，一页。
 *
 * 排序和筛选在后端做，所以「共几次」是筛过之后的数 —— 页数也就跟着筛子走。
 */
export function listRoutineRuns(routineId: string, query: RoutineRunsQuery = {}): Promise<RoutineRunsPage> {
  const q = new URLSearchParams()
  if (query.pageNo) q.set('page_no', String(query.pageNo))
  if (query.perPage) q.set('per_page', String(query.perPage))
  if (query.order) q.set('order', query.order)
  if (query.failed) q.set('failed', 'true')
  if (query.days) q.set('days', String(query.days))
  const suffix = q.toString()
  return request<RoutineRunsPage>(
    `/routines/${encodeURIComponent(routineId)}/runs${suffix ? `?${suffix}` : ''}`
  )
}

export function createRoutine(topicId: string, body: RoutineInput): Promise<Routine> {
  return request<Routine>(`/topics/${encodeURIComponent(topicId)}/routines`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateRoutine(id: string, body: Partial<RoutineInput>): Promise<Routine> {
  return request<Routine>(`/routines/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

/**
 * 确认启用 / 暂停 / 恢复 / 立即执行一次。
 *
 * 确认启用只有规则主人和项目管理员做得成，不是就回 403（`can_manage` 已经说过一次，
 * 这里说第二次是为了「两个人同时看着」这一种）。后三个是频道里的人都能做的。
 */
export function routineAction(
  id: string,
  action: 'confirm' | 'pause' | 'resume' | 'run-now'
): Promise<Routine | RoutineRun> {
  return request<Routine | RoutineRun>(`/routines/${encodeURIComponent(id)}/${action}`, { method: 'POST' })
}

export function deleteRoutine(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/routines/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
