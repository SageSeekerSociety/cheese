// 定时与触发：房间里的 AI 队友按时间、或在项目里发生某件事时自己开工的那些规则。
//
// 这一块从 `api.ts` 拆出来。那个文件已经在上限之上（`.claude/scripts/check-file-sizes.py`），
// 只能变短；而规则本来就有它自己的边界 —— 一条规则属于一个房间、读的人要在这个房间的
// 名册上（`GET /projects/{id}/routines?topic=`），动作只有规则主人和项目管理员能做
// （每一个回答里带回 `can_manage`）—— 所以拆在这里是一个有理由的整体。
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
 * 后三个只有规则主人和项目管理员做得成，都不是就回 403（`can_manage` 已经说过一次，
 * 这里说第二次是为了「两个人同时看着」这一种）。
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
