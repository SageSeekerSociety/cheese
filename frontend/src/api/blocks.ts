import type { Block, ListPayload, ReactionAgg } from '../cx_types'

import { shareInFlight } from '../lib/inflight'

import { request } from './http'

// 机构看板 / Space 看板 (eval F3).
//
// Paging is opt-in on the server: no `limit` returns the WHOLE timeline, which
// is 2.1 MB / 2226 rows on a long topic. The chat panel always passes a limit;
// `has_more` + `oldest_id` walk backwards from there (a cursor, not an offset —
// the tail keeps growing while you read history).
//
// A window can also open in the middle (`around` a message) and walk down towards
// the newest with `after`; `has_newer` + `newest_id` are the cursor that way.
export interface BlockPage extends ListPayload<Block> {
  has_more: boolean
  oldest_id: string | null
  has_newer: boolean
  newest_id: string | null
}

export function listBlocks(
  topicId: string,
  opts?: { limit?: number; before?: string; after?: string; around?: string }
): Promise<BlockPage> {
  const q = new URLSearchParams()
  if (opts?.limit !== undefined) q.set('limit', String(opts.limit))
  if (opts?.before) q.set('before', opts.before)
  if (opts?.after) q.set('after', opts.after)
  if (opts?.around) q.set('around', opts.around)
  const qs = q.toString()
  const query = qs ? `?${qs}` : ''
  const path = `/topics/${encodeURIComponent(topicId)}/blocks${query}`
  // 最新那一页会被两条路同时要：切话题的预取（lib/blockCache 的 refreshBlockCache，
  // 由 router 起头）和对话面板自己那一条（useChatPanel 一进房间就拉）。第二条跟着在
  // 飞的那条走，省下一次重复的 GET。
  //
  // 只合并最新页（不带游标）：带 before/after/around 的那些是用户翻页翻出来的、每一次
  // 都对应当下那一段窗口，合并它们没有好处，还会让两个调用方共享同一份数组。
  const newestPage = !opts?.before && !opts?.after && !opts?.around
  return newestPage ? shareInFlight(`blocks:${path}`, () => request<BlockPage>(path)) : request<BlockPage>(path)
}

// Emoji reactions (Slack semantics): toggles (emoji, caller) on a block and
// returns the block's fresh aggregate. The caller is whoever the session names.
// Other clients get the same aggregate pushed as a `reaction` WS frame on the
// topic channel.
export function toggleReaction(
  blockId: string,
  emoji: string
): Promise<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }> {
  return request<{ toggled: 'added' | 'removed'; reactions: ReactionAgg[] }>(
    `/blocks/${encodeURIComponent(blockId)}/reactions`,
    { method: 'POST', body: JSON.stringify({ emoji }) }
  )
}

// Edit a message you sent. Everyone in the room, you included, also gets the
// edited block as a `block_updated` frame.
export function editMessage(blockId: string, content: string): Promise<Block> {
  return request<Block>(`/blocks/${encodeURIComponent(blockId)}`, {
    method: 'PATCH',
    body: JSON.stringify({ content }),
  })
}
