// 我在房间里的「读」状态：未读角标、已读位、通知级别（静音）、全部标为已读。都按人记，
// 人是谁由凭据说。从 `api.ts` 拆出来（它早就过了行数上限），`api.ts` 原样重新导出。
import { request } from '../api'

// {topic_id: unread_count} for one user; topics with zero unread are omitted.
export function getTopicUnread(projectId: string, handle: string): Promise<Record<string, number>> {
  return request<Record<string, number>>(
    `/projects/${encodeURIComponent(projectId)}/topic-unread?handle=${encodeURIComponent(handle)}`
  )
}

// {peer_handle: unread_count} for one user's 私聊; `cheese` is the 芝士 DM.
// Keyed by peer, not topic id: DM rows come from the member roster, which
// carries no topic id, so getTopicUnread's map cannot address them.
export function getPrivateUnread(projectId: string, handle: string): Promise<Record<string, number>> {
  return request<Record<string, number>>(
    `/projects/${encodeURIComponent(projectId)}/private-unread?handle=${encodeURIComponent(handle)}`
  )
}

// Opening a topic bumps the user's read cursor (clears its badge).
export function markTopicRead(topicId: string, handle: string): Promise<Record<string, string>> {
  return request<Record<string, string>>(`/topics/${encodeURIComponent(topicId)}/read`, {
    method: 'POST',
    body: JSON.stringify({ handle }),
  })
}

/** 我对一间房的通知级别：`all`（默认）或 `mute`（静音：未读不计入任何总数）。 */
export type TopicNotifyLevel = 'all' | 'mute'

/** 我在这个项目里改过通知级别的房间；默认的不列。 */
export function getTopicNotifyLevels(projectId: string): Promise<Record<string, TopicNotifyLevel>> {
  return request<Record<string, TopicNotifyLevel>>(`/projects/${encodeURIComponent(projectId)}/topic-notify-levels`)
}

export function setTopicNotifyLevel(topicId: string, level: TopicNotifyLevel): Promise<unknown> {
  return request(`/topics/${encodeURIComponent(topicId)}/notify-level`, {
    method: 'PUT',
    body: JSON.stringify({ level }),
  })
}

/** 全部标为已读：项目里我每一间有未读的房间。返回动了哪些房间。 */
export function markAllTopicsRead(projectId: string): Promise<{ topic_ids: string[] }> {
  return request<{ topic_ids: string[] }>(`/projects/${encodeURIComponent(projectId)}/read-all`, { method: 'POST' })
}
