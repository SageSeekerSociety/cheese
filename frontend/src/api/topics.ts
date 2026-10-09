import type { ListPayload, RoomTask, Topic } from '../cx_types'

import { readSince, request } from './http'

// `last_activity_at` = 最后活动时间 (the topic's newest block). `updated_at` is
// the row's own mtime and does NOT move when a block lands — it is kept only
// because the API still accepts it.
export type TopicSortField = 'last_activity_at' | 'updated_at' | 'title'
export type TopicSortOrder = 'asc' | 'desc'

// 一份 448 个话题的清单有近 300KB，侧栏每 30 秒问一次：`previous` 是手上那一份，
// 服务端答「没变」（304）时原样交回它，不解析、不换对象、不重画（见 `readSince`）。
export function listTopics(
  projectId: string,
  opts?: { sort?: TopicSortField; order?: TopicSortOrder },
  previous?: ListPayload<Topic>,
  signal?: AbortSignal
): Promise<ListPayload<Topic>> {
  const q = new URLSearchParams({ project_id: projectId })
  if (opts?.sort) q.set('sort', opts.sort)
  if (opts?.order) q.set('order', opts.order)
  return readSince<ListPayload<Topic>>(`/topics?${q.toString()}`, previous, signal)
}

/** 一个话题的名字，和它在哪个项目里。跨项目找话题只要这几样。 */
export type TopicName = Pick<Topic, 'id' | 'project_id' | 'title' | 'kind' | 'status' | 'members_only'>

/** 我能看到的所有项目里的话题名，最近有动静的在前。私聊不在里面。 */
export async function listTopicNames(): Promise<TopicName[]> {
  return (await request<{ topics: TopicName[] }>('/topics/names')).topics
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
