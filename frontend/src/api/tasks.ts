// 任务：一个人负责、和 AI 队友在自己的对话里做成的一件事，挂在它所在的房间下。
// 任务就是一段对话，地址用它自己的 id（`/topics/{task}/…`）；谁能看任务，由谁能进它
// 所在的房间决定。列出、新建任务和 AI 的提议挂在房间下。
import type { RoomTask } from '../cx_types'

import { request } from './http'

function taskPath(taskId: string): string {
  return `/topics/${encodeURIComponent(taskId)}`
}

/** 一个任务。它的对话和房间的一样读（`/topics/{task}/blocks`）。 */
export function getTask(taskId: string): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(taskId)}/task`)
}

/** 新建任务：创建的人就是负责人。 */
export function createRoomTask(roomId: string, title?: string): Promise<RoomTask> {
  return request<RoomTask>(`/topics/${encodeURIComponent(roomId)}/tasks`, {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })
}

/** 开始：负责人确认讨论清楚了。项目没有默认审阅人时要指定一位。 */
export function startTask(taskId: string, reviewerHandle?: string | null): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(taskId)}/start`, {
    method: 'POST',
    body: JSON.stringify(reviewerHandle ? { reviewer_handle: reviewerHandle } : {}),
  })
}

/** 转交：换负责人，或换做它的 AI 队友。 */
export function updateTask(
  taskId: string,
  change: { owner_handle?: string; agent_handle?: string | null; contributor_handles?: string[] }
): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(taskId)}/task`, {
    method: 'PATCH',
    body: JSON.stringify(change),
  })
}

/** 给任务改名。人起的名字平台之后不再改。 */
export function renameTask(taskId: string, title: string): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(taskId)}/title`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

/** 关闭任务。带结论是做完了，不带是不做了。 */
export function closeTask(taskId: string, conclusion?: string): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(taskId)}/close`, {
    method: 'POST',
    body: JSON.stringify(conclusion ? { conclusion } : {}),
  })
}

/** 重新打开一件关了的任务。交付过的从项目最新的代码接着做。 */
export function reopenTask(taskId: string): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(taskId)}/reopen`, { method: 'POST' })
}

export interface DocumentComparison {
  before: { version: number; content: string }
  after: { version: number; content: string }
}

/** 文档的两个版本，并排读。`after` 不给就是当前版本；版本 0 是什么都还没写。 */
export function compareDocumentVersions(
  documentId: string,
  before: number,
  after?: number
): Promise<DocumentComparison> {
  const q = new URLSearchParams({ before: String(before) })
  if (after != null) q.set('after', String(after))
  return request<DocumentComparison>(`/documents/${encodeURIComponent(documentId)}/compare?${q.toString()}`)
}

/** 任务从哪来：转出它的讨论，和讨论里用到的材料。 */
export function getTaskRelated(taskId: string): Promise<import('../types/taskOrigin').TaskRelated> {
  return request(`${taskPath(taskId)}/related`)
}

/** 第一轮整理文档失败后，把那条指令再交给 AI 队友一次。 */
export function retryTaskOpening(taskId: string): Promise<{ opening: string }> {
  return request(`${taskPath(taskId)}/opening`, { method: 'POST' })
}
