// 任务：一个人负责、和 AI 队友在自己的对话里做成的一件事，挂在它所在的房间下。
// 任务就是一段对话，地址用它自己的 id（`/topics/{task}/…`）；谁能看任务，由谁能进它
// 所在的房间决定。列出、新建任务和 AI 的提议挂在房间下。
import type { ListPayload, RoomTask } from '../cx_types'

import { readSince, request } from './http'

function taskPath(taskId: string): string {
  return `/topics/${encodeURIComponent(taskId)}`
}

// 两份任务清单（整个项目的、一个房间的）都很沉：这个项目 1373 条活 2 MB 出头，房间
// 那份 `limit=0` 也还有 1 MB 上下。`previous` 是手上那一份，没变时服务端答 304，原样
// 交回它（见 `readSince`）。
// 整个项目的支线，每条带着它当前骑的那张验收卡。侧栏要画「房间 → 它派出去的活
// → 那件活的 PR」这棵树，而按房间问是一个房间一个请求（这里有一百七十多个）。
// `open` 只要还在进行的：侧栏只挂这些，而已经关掉的是它们的十几倍。
export function listProjectTasks(
  projectId: string,
  opts: { open?: boolean } = {},
  previous?: ListPayload<RoomTask>,
  signal?: AbortSignal
): Promise<ListPayload<RoomTask>> {
  const path = `/projects/${encodeURIComponent(projectId)}/tasks`
  return readSince(opts.open ? `${path}?status=open` : path, previous, signal)
}

/** 「全部任务」里谁的：我负责的、我协助的、别人的。 */
export type Whose = 'mine' | 'helping' | 'others'
/** 「全部任务」那一排筛选：全部，或其中一种谁的。 */
export type TaskFilter = 'all' | Whose

export interface ProjectTaskPage extends ListPayload<RoomTask> {
  has_more: boolean
  /** 下一页的游标：传给 `before`。 */
  next: string | null
  /** 同一状态、同一频道下一共几件，按谁的分。 */
  counts: Record<'all' | Whose, number>
}

// 项目里某一种状态的活，一页一页读：最近有动静的在前。已经结束的只会越积越多，「全部
// 任务」往下滚到哪读到哪，不整份读。频道、谁的都在服务端筛，计数也是服务端数的。
export function pageProjectTasks(
  projectId: string,
  opts: {
    status: 'open' | 'closed'
    limit: number
    before?: string | null
    channel?: string | null
    whose?: Whose | null
  }
): Promise<ProjectTaskPage> {
  const q = new URLSearchParams({ status: opts.status, limit: String(opts.limit) })
  if (opts.before) q.set('before', opts.before)
  if (opts.channel) q.set('channel', opts.channel)
  if (opts.whose) q.set('whose', opts.whose)
  return request<ProjectTaskPage>(`/projects/${encodeURIComponent(projectId)}/tasks?${q.toString()}`)
}

/** Tasks in this room, each with its own branch and delivery — narrowed to what
 *  the caller draws (`GET /topics/{id}/tasks` says what each option keeps). */
export function listRoomTasks(
  roomId: string,
  opts?: {
    // 每条支线最多带回多少块对话。画卡片、画概览的调用方一个块都不看，所以取 0 ——
    // 后端对 0 直接跳过取块的那一次查询。不传的话后端会把每条支线的全部历史都吐回来
    // （它自己的 docstring 说明了为什么没有默认上限）。
    limit?: number
    status?: 'open' | 'closed'
    /** 最新的几件：已结束的按结束时间，否则按新建时间，新的在前。 */
    latest?: number
    ids?: string[]
    /** 这几块带着的任务：从它们拆出去的，或它们说在这里开始的。 */
    blocks?: string[]
    /** 只要有自己分支的。 */
    branch?: boolean
  },
  previous?: ListPayload<RoomTask>,
  signal?: AbortSignal
): Promise<ListPayload<RoomTask>> {
  const q = new URLSearchParams()
  if (opts?.limit != null) q.set('limit', String(opts.limit))
  if (opts?.status) q.set('status', opts.status)
  if (opts?.latest != null) q.set('latest', String(opts.latest))
  for (const id of opts?.ids ?? []) q.append('ids', id)
  for (const id of opts?.blocks ?? []) q.append('blocks', id)
  if (opts?.branch) q.set('branch', 'true')
  const query = q.toString() ? `?${q.toString()}` : ''
  return readSince(`/topics/${encodeURIComponent(roomId)}/tasks${query}`, previous, signal)
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
