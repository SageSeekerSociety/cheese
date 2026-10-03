import type { AskGroupData, AskGroupScope, AskGroupSubmission } from '../lib/askGroup'

import { request } from '../api'
import { assertGroupData } from '../lib/askGroup'

// Fixed contract: ask-ux-preview/group-api.md at 3f88423c. These are real
// endpoints, not a single-answer loop; 404/501 remain visible unavailable states
// until S's backend lands in the same release.
export async function readAskGroup(scope: AskGroupScope): Promise<AskGroupData> {
  const query = new URLSearchParams({ topic_id: scope.topic_id, asked_by: scope.asked_by })
  const data = await request<AskGroupData>(`/topics/asks/${encodeURIComponent(scope.id)}?${query}`)
  assertGroupData(data, scope)
  return data
}

export async function settleAskGroup(scope: AskGroupScope, payload: AskGroupSubmission): Promise<AskGroupData> {
  const data = await request<AskGroupData>(`/topics/asks/${encodeURIComponent(scope.id)}/settle`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  assertGroupData(data, scope)
  return data
}

/** A view of one open group: the registration fields plus a member block id to
 *  anchor on, so the panel can take over without the member blocks loaded. */
export interface AwaitingAskGroup extends AskGroupScope {
  anchor: string
}

function isAwaitingGroup(value: unknown): value is AwaitingAskGroup {
  if (!value || typeof value !== 'object') return false
  const group = value as Record<string, unknown>
  return (
    typeof group.topic_id === 'string' &&
    typeof group.asked_by === 'string' &&
    typeof group.id === 'string' &&
    Array.isArray(group.members) &&
    group.members.every((id) => typeof id === 'string') &&
    typeof group.total === 'number' &&
    typeof group.anchor === 'string'
  )
}

/** The open groups this room owes the caller an answer, asked once on entry.
 *
 *  The timeline loads a recent window, so a group asked long ago is not among
 *  the blocks `useAskGroups` registers from; this is the read that lets the
 *  composer takeover appear from the first frame regardless. Anything the
 *  reader cannot recognise is dropped rather than trusted. */
export async function listAwaitingAskGroups(topicId: string): Promise<AwaitingAskGroup[]> {
  const data = await request<{ groups?: unknown }>(`/topics/${encodeURIComponent(topicId)}/asks/awaiting`)
  const groups = data?.groups
  return Array.isArray(groups) ? groups.filter(isAwaitingGroup) : []
}
