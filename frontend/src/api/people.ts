import type { MemberSummary, ProfileTopic, Topic, UserProfile } from '../cx_types'

import { request } from './http'

// A 1:1 private chat as a normal Topic (open the chat WS on its id). `peerHandle`
// is a person-to-person DM between the two humans (shared by both); `agentHandle`
// is the member's 1:1 with that AI teammate — one room per teammate, and it stays
// that teammate's even after the project's default changes. Neither → the
// project's default teammate.
export function getPrivateChat(
  projectId: string,
  userHandle: string,
  peerHandle?: string,
  agentHandle?: string
): Promise<Topic> {
  const peer = peerHandle ? `&peer_handle=${encodeURIComponent(peerHandle)}` : ''
  const agent = agentHandle ? `&agent_handle=${encodeURIComponent(agentHandle)}` : ''
  return request<Topic>(
    `/projects/${encodeURIComponent(projectId)}/private-chat?user_handle=${encodeURIComponent(userHandle)}${peer}${agent}`
  )
}

// 成员页 / portfolio (spec §7.2): what a member started + what awaits them.
export function getMemberSummary(projectId: string, handle: string): Promise<MemberSummary> {
  return request<MemberSummary>(
    `/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(handle)}/summary`
  )
}

// 个人主页 / LinkedIn-GitHub profile (spec §1, §7.2). Cross-project résumé:
// who they are, a year of activity, their projects, and — on your own page —
// what 芝士 has noted about you.
export function getUserProfile(handle: string): Promise<UserProfile> {
  return request<UserProfile>(`/users/${encodeURIComponent(handle)}/profile`)
}

// The topics a person wrote in, latest participation first. `from`/`to` are
// UTC dates (`YYYY-MM-DD`), both included.
export function getUserTopics(
  handle: string,
  range: { from?: string; to?: string; limit?: number } = {}
): Promise<{ topics: ProfileTopic[] }> {
  const query = new URLSearchParams()
  if (range.from) query.set('from', range.from)
  if (range.to) query.set('to', range.to)
  if (range.limit) query.set('limit', String(range.limit))
  const qs = query.toString()
  return request<{ topics: ProfileTopic[] }>(`/users/${encodeURIComponent(handle)}/topics${qs ? `?${qs}` : ''}`)
}

// Delete one thing an agent noted about the signed-in person.
export function deleteUnderstanding(id: string): Promise<{ deleted: string }> {
  return request<{ deleted: string }>(`/users/me/understanding/${encodeURIComponent(id)}`, { method: 'DELETE' })
}
