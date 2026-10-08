import type { AcceptCard, ListPayload, PrChecks } from '../cx_types'

import { BASE, request } from './http'

// ---- 采纳卡 / 验收 (eval C5/A3) ----

// Accept cards for a topic, newest first.
// A task's id lists that task's cards; a room's, every card of its tasks.
export function getAcceptCards(topicId: string): Promise<ListPayload<AcceptCard>> {
  return request<ListPayload<AcceptCard>>(`/topics/${encodeURIComponent(topicId)}/accept-card`)
}

// 采纳 PR 化 (#188 §5.1): live CI state of the newest card's PR. Safe to poll —
// answers {available:false} when the topic has no PR-riding card.
export function getPrChecks(topicId: string): Promise<PrChecks> {
  return request<PrChecks>(`/topics/${encodeURIComponent(topicId)}/pr-checks`)
}

/** 这张卡交出去的那一份字节。快照在递卡那一刻就落下来了，所以人点采纳之前就取得
 *  到——他要审的正是这一份。 */
export function cardDeliverableUrl(cardId: string): string {
  return `${BASE}/accept-cards/${encodeURIComponent(cardId)}/deliverable`
}

// 合的是人看到的那个 commit：会触发合并的三个入口（采纳 / 人工放行 / 布防）都
// 带上卡片渲染时 `merge_state.head_sha` 的值。轮询器每分钟把卡刷到 PR 的新
// head，屏幕上那份不会自己变——不声明看的是哪一版，点下去合的就可能是一段没人
// 看过的代码。后端拿它和卡当前的 head 对，不一致就 422 让人重新看过。
// null 是合法值：卡还没被镜像过 head，或者根本不骑 PR（平台 lane）。
export function acceptCard(cardId: string, decidedBy: string, headSha: string | null): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/accept`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy, head_sha: headSha }),
  })
}

export function rejectCard(cardId: string, decidedBy: string, note: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/reject`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy, note }),
  })
}

// 作废：结束一张未决的卡，不合并也不退回。作废人由后端从会话认定；验收人、
// 项目所有者、团队管理员能作废（server-side）。
export function voidCard(cardId: string, note: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/void`, {
    method: 'POST',
    body: JSON.stringify({ note }),
  })
}

// 撤回采纳 (spec §6.3: 采纳可撤销). Revoke an accepted card → un-archives the
// topic. Only the accepter / owner / lead may revoke (enforced server-side).
export function revokeCard(cardId: string, decidedBy: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/revoke`, {
    method: 'POST',
    body: JSON.stringify({ decided_by: decidedBy }),
  })
}

// 人工放行 (#718): merge a pending card's PR even though its merge state is
// not clean. The platform never does this on its own —红着合有时候是对的，
// 不能接受的是没有人做过这个决定。So the actor is taken from the session
// server-side (never the body) and the card records who / when / what the
// checks said / why. Only the project's override list (owner/lead when
// unconfigured) may call it, and 芝士 is refused outright.
export function mergeCardAnyway(cardId: string, reason: string, headSha: string | null): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/merge-anyway`, {
    method: 'POST',
    body: JSON.stringify({ reason, head_sha: headSha }),
  })
}

// 绿了自动合 (#718): arm/disarm auto-merge on a pending card. Reviewer-side
// switch, only meaningful on a project with auto_merge_allowed; the actor is
// the session user server-side, and 新提交作废采纳 disarms it again.
export function setAutoMerge(cardId: string, enabled: boolean, headSha: string | null): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/auto-merge`, {
    method: 'POST',
    body: JSON.stringify({ enabled, head_sha: headSha }),
  })
}

// 改验收人 (spec §4.4: 任何成员都可以改推荐/加人). Reassign a pending card to
// another reviewer; what the deliverer asked them to check stays as it was.
export function reassignCard(cardId: string, reviewerHandle: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/reassign`, {
    method: 'POST',
    body: JSON.stringify({
      reviewer_handle: reviewerHandle,
    }),
  })
}

// 主分支保护 (spec §4.4): record one approval toward the card's accept. The
// backend enforces "AI 不能投票" and per-person uniqueness.
export function approveCard(cardId: string, approverHandle: string): Promise<AcceptCard> {
  return request<AcceptCard>(`/accept-cards/${encodeURIComponent(cardId)}/approve`, {
    method: 'POST',
    body: JSON.stringify({ approver_handle: approverHandle }),
  })
}
