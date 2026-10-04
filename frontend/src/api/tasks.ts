// 任务：一个人负责、和 AI 队友在自己的对话里做成的一件事，挂在它所在的房间下。
// 地址都经过房间（`/topics/{room}/tasks/{task}`）：谁能看任务，由谁能进房间决定。
import type { Block, RoomTask } from '../cx_types'

import { request } from './http'

type TaskWithBlocks = RoomTask & { blocks: Block[] }

function taskPath(roomId: string, taskId: string): string {
  return `/topics/${encodeURIComponent(roomId)}/tasks/${encodeURIComponent(taskId)}`
}

/** 一个任务，连着它自己的对话。`limit` 只截对话，任务本身照常整份回来。 */
export function getRoomTask(
  roomId: string,
  taskId: string,
  opts?: { limit?: number; through?: string }
): Promise<TaskWithBlocks> {
  const q = new URLSearchParams()
  if (opts?.limit != null) q.set('limit', String(opts.limit))
  if (opts?.through) q.set('through', opts.through)
  const query = q.toString() ? `?${q.toString()}` : ''
  return request<TaskWithBlocks>(`${taskPath(roomId, taskId)}${query}`)
}

/** 负责人在任务里说话，直接送到做这个任务的 AI 队友。别人说话后端会拒绝。 */
export function sayOnRoomTask(roomId: string, taskId: string, content: string, requestId?: string): Promise<Block> {
  return request<Block>(`${taskPath(roomId, taskId)}/messages`, {
    method: 'POST',
    body: JSON.stringify({ content, request_id: requestId ?? crypto.randomUUID() }),
  })
}

/** 新建任务：创建的人就是负责人。 */
export function createRoomTask(roomId: string, title?: string): Promise<RoomTask> {
  return request<RoomTask>(`/topics/${encodeURIComponent(roomId)}/tasks`, {
    method: 'POST',
    body: JSON.stringify(title ? { title } : {}),
  })
}

/** 开始：负责人确认讨论清楚了。项目没有默认审阅人时要指定一位。 */
export function startRoomTask(roomId: string, taskId: string, reviewerHandle?: string | null): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(roomId, taskId)}/start`, {
    method: 'POST',
    body: JSON.stringify(reviewerHandle ? { reviewer_handle: reviewerHandle } : {}),
  })
}

/** 转交：换负责人，或换做它的 AI 队友。 */
export function updateRoomTask(
  roomId: string,
  taskId: string,
  change: { owner_handle?: string; agent_handle?: string | null }
): Promise<RoomTask> {
  return request<RoomTask>(taskPath(roomId, taskId), {
    method: 'PATCH',
    body: JSON.stringify(change),
  })
}

/** 关闭任务。带结论是做完了，不带是不做了。 */
export function closeRoomTask(roomId: string, taskId: string, conclusion?: string): Promise<RoomTask> {
  return request<RoomTask>(`${taskPath(roomId, taskId)}/close`, {
    method: 'POST',
    body: JSON.stringify(conclusion ? { conclusion } : {}),
  })
}

/** 任务的实况文档是哪一份；第一次问时建一份空的。 */
export function getTaskDocumentId(roomId: string, taskId: string): Promise<string> {
  return request<{ id: string }>(`${taskPath(roomId, taskId)}/document`).then((doc) => doc.id)
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

/** AI 队友提议的一个任务，等房间里的人决定。 */
export interface TaskProposal {
  id: string
  room_id: string
  title: string
  summary: string
  proposed_by: string
  state: 'open' | 'accepted' | 'dismissed'
  task_id: string | null
  created_at: string
}

function proposalPath(roomId: string, proposalId?: string): string {
  const base = `/topics/${encodeURIComponent(roomId)}/task-proposals`
  return proposalId ? `${base}/${encodeURIComponent(proposalId)}` : base
}

/** 这个房间里还在等人决定的提议。 */
export function listTaskProposals(roomId: string): Promise<TaskProposal[]> {
  return request<TaskProposal[]>(proposalPath(roomId))
}

/** 从 AI 队友的提议创建任务：点的人就是负责人。 */
export function acceptTaskProposal(roomId: string, proposalId: string): Promise<RoomTask> {
  return request<RoomTask>(`${proposalPath(roomId, proposalId)}/accept`, { method: 'POST' })
}

/** 不采用这条提议。 */
export function dismissTaskProposal(roomId: string, proposalId: string): Promise<TaskProposal> {
  return request<TaskProposal>(`${proposalPath(roomId, proposalId)}/dismiss`, { method: 'POST' })
}
