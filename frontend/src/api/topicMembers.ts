// 话题名册的三种写法：加人、改角色、移出。`actor` 是动手的人的 handle，后端按他在
// 这个话题里的角色（owner / admin 才能管名册）放行。
//
// 写成功之后都喊一声（`lib/topicRosterChanges.ts`）：对话栏手上那份名册是另外拉的，
// 不喊它就一直是旧的——刚加进来的人 @ 不出来。读名册（`listTopicMembers`）留在
// `api.ts`，它走那里的房间读去重。
import type { TopicMemberRow } from '../cx_types'

import { request } from '../api'
import { announceTopicRosterChange } from '../lib/topicRosterChanges'

function rosterWrite<T>(topicId: string, write: Promise<T>): Promise<T> {
  return write.then((result) => {
    announceTopicRosterChange(topicId)
    return result
  })
}

export function addTopicMember(topicId: string, handle: string, role: string, actor: string): Promise<TopicMemberRow> {
  return rosterWrite(
    topicId,
    request<TopicMemberRow>(`/topics/${encodeURIComponent(topicId)}/members`, {
      method: 'POST',
      body: JSON.stringify({ handle, role, actor }),
    })
  )
}

export function updateTopicMemberRole(
  topicId: string,
  handle: string,
  role: string,
  actor: string
): Promise<TopicMemberRow> {
  return rosterWrite(
    topicId,
    request<TopicMemberRow>(`/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(handle)}`, {
      method: 'PUT',
      body: JSON.stringify({ role, actor }),
    })
  )
}

export function removeTopicMember(topicId: string, handle: string, actor: string): Promise<{ deleted: boolean }> {
  return rosterWrite(
    topicId,
    request<{ deleted: boolean }>(
      `/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(handle)}?actor=${encodeURIComponent(actor)}`,
      { method: 'DELETE' }
    )
  )
}
