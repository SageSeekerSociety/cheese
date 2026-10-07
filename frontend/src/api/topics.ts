import type { Block, ListPayload, RoomTask, Topic } from '../cx_types'

import { request, requestConditional, roomRead } from './http'

// `last_activity_at` = 最后活动时间 (the topic's newest block). `updated_at` is
// the row's own mtime and does NOT move when a block lands — it is kept only
// because the API still accepts it.
export type TopicSortField = 'last_activity_at' | 'updated_at' | 'title'
export type TopicSortOrder = 'asc' | 'desc'

// 上一次读到的话题清单和它的 ETag，按请求路径记着。侧栏每 30s 轮询一次，一份 448
// 个话题的清单有近 300KB：服务端答「没变」（304）时把手里这同一个 payload 原样交回，
// 调用方的 `topics.value = payload.data` 就是一次同引用的赋值 —— Vue 的 ref setter
// 见到同一个对象会跳过触发（不解析、不换数组、不重画）。变了才落新的一份。
const topicListCache = new Map<string, { etag: string | null; payload: ListPayload<Topic> }>()

export function listTopics(
  projectId: string,
  opts?: { sort?: TopicSortField; order?: TopicSortOrder }
): Promise<ListPayload<Topic>> {
  const q = new URLSearchParams({ project_id: projectId })
  if (opts?.sort) q.set('sort', opts.sort)
  if (opts?.order) q.set('order', opts.order)
  const path = `/topics?${q.toString()}`
  const cached = topicListCache.get(path)
  // 带上上一次那版 ETag 去问。服务端算出的一模一样就回 304（见后端 list_topics）。
  return requestConditional<ListPayload<Topic>>(path, cached?.etag ?? null).then((result) => {
    if (result.notModified) {
      if (cached) return cached.payload
      // 304 但手里没留底（比如刚重启、缓存已清）：退回一次无条件读，别把空手当没变。
      return request<ListPayload<Topic>>(path)
    }
    if (!result.data) throw new Error('empty topic list response')
    topicListCache.set(path, { etag: result.etag, payload: result.data })
    return result.data
  })
}

/** 一个话题的名字，和它在哪个项目里。跨项目找话题只要这几样。 */
export type TopicName = Pick<Topic, 'id' | 'project_id' | 'title' | 'kind' | 'status' | 'members_only'>

/** 我能看到的所有项目里的话题名，最近有动静的在前。私聊不在里面。 */
export async function listTopicNames(): Promise<TopicName[]> {
  return (await request<{ topics: TopicName[] }>('/topics/names')).topics
}

// 整个项目的支线，每条带着它当前骑的那张验收卡。侧栏要画「房间 → 它派出去的活
// → 那件活的 PR」这棵树，而按房间问是一个房间一个请求（这里有一百七十多个）。
export function listProjectTasks(projectId: string): Promise<ListPayload<RoomTask>> {
  return request<ListPayload<RoomTask>>(`/projects/${encodeURIComponent(projectId)}/tasks`)
}

/** Tasks in this room, each with its own branch and delivery. */
export function listRoomTasks(
  roomId: string,
  // 每条支线最多带回多少块对话。标记只要支线本身，所以取 1 —— 不传的话后端会把
  // 房间里每条支线的全部历史都吐回来（它自己的 docstring 说明了为什么没有默认上限）。
  opts?: { limit?: number }
): Promise<ListPayload<RoomTask & { blocks: Block[] }>> {
  const q = new URLSearchParams()
  if (opts?.limit != null) q.set('limit', String(opts.limit))
  const query = q.toString() ? `?${q.toString()}` : ''
  return roomRead<ListPayload<RoomTask & { blocks: Block[] }>>(`/topics/${encodeURIComponent(roomId)}/tasks${query}`)
}

export function createTopic(pid: string, title: string, description?: string, membersOnly = false): Promise<Topic> {
  const body: Record<string, string | boolean> = { project_id: pid, title, members_only: membersOnly }
  if (description) body.description = description
  return request<Topic>('/topics', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

/** A person names the room. The platform stops renaming it on its own from then on. */
export function setTopicTitle(topicId: string, title: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/title`, {
    method: 'POST',
    body: JSON.stringify({ title }),
  })
}

export type TaskNamingMode = 'auto' | 'manual'
export interface TaskNaming {
  mode: TaskNamingMode
  /** Whether the deployment can name tasks at all (a model gateway is configured). */
  available: boolean
  can_manage: boolean
}

export function getTaskNaming(projectId: string): Promise<TaskNaming> {
  return request<TaskNaming>(`/projects/${encodeURIComponent(projectId)}/task-naming`)
}

export function setTaskNaming(projectId: string, mode: TaskNamingMode): Promise<TaskNaming> {
  return request<TaskNaming>(`/projects/${encodeURIComponent(projectId)}/task-naming`, {
    method: 'PUT',
    body: JSON.stringify({ mode }),
  })
}

// ---- 归档去向: manual archive / unarchive ----

export function archiveTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/archive`, {
    method: 'POST',
  })
}

export function unarchiveTopic(topicId: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/unarchive`, {
    method: 'POST',
  })
}

/** 把频道里的一条消息转为这个频道的一个任务，点的人是负责人（由会话认，不由
 *  请求体说）。私聊里的消息不能转。 */
export function upgradeBlock(blockId: string): Promise<RoomTask> {
  return request<RoomTask>(`/blocks/${encodeURIComponent(blockId)}/upgrade`, {
    method: 'POST',
    body: JSON.stringify({}),
  })
}
