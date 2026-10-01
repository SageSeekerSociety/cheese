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
