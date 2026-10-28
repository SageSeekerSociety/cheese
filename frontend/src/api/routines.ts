// 例行事务：房间里的 AI 队友按时间、或在项目里发生某件事时自己开工的那些规则。
//
// 这一块从 `api.ts` 拆出来。那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；而规则本来就有它自己的边界 —— 一条规则属于一个房间、读的人要在这个房间的
// 名册上（`GET /projects/{id}/routines?topic=`），读得到就改得动，只有删除收窄到规则
// 主人和项目管理员（每一个回答里带回 `can_delete`）—— 所以拆在这里是一个有理由的整体。
//
// 形状（`Routine` / `RoutineRun` / 那几张措辞表）在 `lib/routine.ts`：组件要吃那个形状
// 又碰不得 API 层，取数这一半只往上面接。
import type { ListPayload } from '../cx_types'
import type { Routine, RoutineInput, RoutineRun } from '../lib/routine'
import type { RoutineTemplate, RoutineTemplateInput } from '../lib/routineTemplates'

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

/**
 * 这条规则的一页执行记录，「看全部」那一页翻着读。
 *
 * `failedOnly` 问的是「哪几次没成」：失败和未执行两种一起来（后端那一边）。回来的
 * `total` 是**筛过之后的全部**，翻页器上那句「共 N 次」说的是它。
 *
 * `credits_this_month` 是**整条规则**本月的点数，不是这一页的 —— 翻到第几页都一样。
 * 每一行自己的那一次在 `runs[].credits` 上（`null` 是还没结算，不是 0）。
 */
export function listRoutineRuns(
  id: string,
  opts: { offset?: number; limit?: number; failedOnly?: boolean } = {}
): Promise<ListPayload<RoutineRun> & { credits_this_month: number }> {
  const q = new URLSearchParams()
  if (opts.offset) q.set('offset', String(opts.offset))
  if (opts.limit) q.set('limit', String(opts.limit))
  if (opts.failedOnly) q.set('failed_only', 'true')
  const suffix = q.toString()
  return request<ListPayload<RoutineRun> & { credits_this_month: number }>(
    `/routines/${encodeURIComponent(id)}/runs${suffix ? `?${suffix}` : ''}`
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
 * 暂停 / 恢复 / 立即执行一次。
 *
 * 三个都要读得到这条规则：读得到就改得动。谁读得到是后端凭这次请求的身份判断的
 * （这个房间的名册成员，或者管理这个项目的人），前端不猜第二遍。
 */
export function routineAction(id: string, action: 'pause' | 'resume' | 'run-now'): Promise<Routine | RoutineRun> {
  return request<Routine | RoutineRun>(`/routines/${encodeURIComponent(id)}/${action}`, { method: 'POST' })
}

export function deleteRoutine(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/routines/${encodeURIComponent(id)}`, { method: 'DELETE' })
}

/**
 * 「存成模板」那一条：读这个项目存下来的、存一条、改一条、删一条。
 *
 * 挂在项目上，不挂房间：模板是一张还没落到任何房间里的表，同一个项目里谁存下来的都
 * 在同一个列表里，谁读得到项目就读得到、也就存得下、改得动、删得掉。所以这里没有
 * `can_delete`。
 */
export function listRoutineTemplates(projectId: string): Promise<ListPayload<RoutineTemplate>> {
  return request<ListPayload<RoutineTemplate>>(`/projects/${encodeURIComponent(projectId)}/routine-templates`)
}

export function createRoutineTemplate(projectId: string, body: RoutineTemplateInput): Promise<RoutineTemplate> {
  return request<RoutineTemplate>(`/projects/${encodeURIComponent(projectId)}/routine-templates`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateRoutineTemplate(id: string, body: RoutineTemplateInput): Promise<RoutineTemplate> {
  return request<RoutineTemplate>(`/routine-templates/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(body),
  })
}

export function deleteRoutineTemplate(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/routine-templates/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
