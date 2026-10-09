// 频道里有谁的几种写法：管理者加人、移出，本人加入、退出，管理者写频道说明。动手的人
// 由凭据说，后端按「频道创建者或项目管理员」放行（加人、移出、说明），加入和退出
// 只认本人。
//
// 名册变了就把缓存里那份名册标过期（`query/room`）：房间头部、对话栏的 @ 候选、
// 成员面板读的都是它，不标的话刚加进来的人 @ 不出来。
import type { Topic, TopicMemberRow } from '../cx_types'

import { request } from '../api'

import { queryClient } from '@/query/client'
import { keys } from '@/query/keys'

function rosterWrite<T>(topicId: string, write: Promise<T>): Promise<T> {
  return write.then((result) => {
    void queryClient.invalidateQueries({ queryKey: keys.roomMembers(topicId) })
    return result
  })
}

export function addTopicMember(topicId: string, handle: string): Promise<TopicMemberRow> {
  return rosterWrite(
    topicId,
    request<TopicMemberRow>(`/topics/${encodeURIComponent(topicId)}/members`, {
      method: 'POST',
      body: JSON.stringify({ handle }),
    })
  )
}

export function removeTopicMember(topicId: string, handle: string): Promise<{ deleted: boolean }> {
  return rosterWrite(
    topicId,
    request<{ deleted: boolean }>(`/topics/${encodeURIComponent(topicId)}/members/${encodeURIComponent(handle)}`, {
      method: 'DELETE',
    })
  )
}

/** 我加入一个频道。「综合」不用加入。 */
export function joinChannel(topicId: string): Promise<{ joined: boolean }> {
  return rosterWrite(
    topicId,
    request<{ joined: boolean }>(`/topics/${encodeURIComponent(topicId)}/join`, { method: 'POST' })
  )
}

/** 我退出一个频道。频道照样看得见，只是不在我的侧栏里了。 */
export function leaveChannel(topicId: string): Promise<{ joined: boolean }> {
  return rosterWrite(
    topicId,
    request<{ joined: boolean }>(`/topics/${encodeURIComponent(topicId)}/leave`, { method: 'POST' })
  )
}

/** 频道说明，空串清掉。 */
export function setChannelDescription(topicId: string, description: string): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/description`, {
    method: 'PUT',
    body: JSON.stringify({ description }),
  })
}

/** 把频道设为私密，或重新公开。设为私密的人留在频道里。 */
export function setChannelMembersOnly(topicId: string, membersOnly: boolean): Promise<Topic> {
  return request<Topic>(`/topics/${encodeURIComponent(topicId)}/members-only`, {
    method: 'PUT',
    body: JSON.stringify({ members_only: membersOnly }),
  })
}
