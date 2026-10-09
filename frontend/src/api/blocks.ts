import type { Block, ListPayload, ReactionAgg } from '../cx_types'

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
  opts?: {
    limit?: number
    before?: string
    after?: string
    around?: string
    /** 编号（`seq`）在这之后存进来的，按存进来的先后：手里到这个号为止的页面漏了的那些。 */
    storedAfter?: number
  }
): Promise<BlockPage> {
  // 对话栏只读房间里显示的那些：一个干着活的房间，块大多是队友干活的步骤（现场读
  // 它们，走 socket 和 `/transcript`），不筛的话一页里多半没有一行画得出来。
  const q = new URLSearchParams({ shown: 'true' })
  if (opts?.limit !== undefined) q.set('limit', String(opts.limit))
  if (opts?.before) q.set('before', opts.before)
  if (opts?.after) q.set('after', opts.after)
  if (opts?.around) q.set('around', opts.around)
  if (opts?.storedAfter !== undefined) q.set('stored_after', String(opts.storedAfter))
  const qs = q.toString()
  const query = qs ? `?${qs}` : ''
  const path = `/topics/${encodeURIComponent(topicId)}/blocks${query}`
  return request<BlockPage>(path)
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
