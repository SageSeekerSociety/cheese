// Per-topic timeline cache shared between the chat panel (instant render on
// switch-back — no blank flash) and the unread poll (background refresh, so a
// reply that landed while you were in ANOTHER topic is already in the cache
// when you come back: the last message is there on the very first frame).
//
// What it holds is a WINDOW, not the whole timeline: the newest page, extended
// upwards as the user scrolls back. `blockHasMore` says whether older blocks
// still exist above the cached slice.
import type { Block } from '../cx_types'

import { listBlocks } from '../api'

import { mergeRefreshedTail, PAGE_SIZE } from './blockPaging'

export const blockCache = new Map<string, Block[]>()
// Kept as a sidecar map rather than folded into the entry so `blockCache` stays
// a plain Map<topicId, Block[]> — that shape is what probe scripts read.
export const blockHasMore = new Map<string, boolean>()

// Dev-only observability hook: lets probe scripts (scripts/probe_flash.py)
// inspect the REAL cache instance — a dynamic import from the console/probe
// can resolve to a second module instance under Vite HMR, which lies.
declare global {
  interface Window {
    __blockCache?: Map<string, Block[]>
  }
}
if (import.meta.env.DEV) window.__blockCache = blockCache

export function cachedWindow(topicId: string): { blocks: Block[]; hasMore: boolean } | null {
  const blocks = blockCache.get(topicId)
  if (!blocks) return null
  return { blocks, hasMore: blockHasMore.get(topicId) ?? false }
}

export function setCachedWindow(topicId: string, window: { blocks: Block[]; hasMore: boolean }): void {
  blockCache.set(topicId, window.blocks)
  blockHasMore.set(topicId, window.hasMore)
}

export async function refreshBlockCache(topicId: string): Promise<Block[] | null> {
  try {
    // Only the newest page — this runs for every topic whose unread count grew,
    // and refetching 2.1 MB per topic in the background is what we are fixing.
    const payload = await listBlocks(topicId, { limit: PAGE_SIZE })
    const fresh = { blocks: payload.data, hasMore: payload.has_more }
    const cached = cachedWindow(topicId)
    // Merge, don't overwrite: the user may have paged back through this topic
    // already, and that scrollback must survive a background refresh.
    const merged = cached ? mergeRefreshedTail(cached, fresh) : fresh
    setCachedWindow(topicId, merged)
    return merged.blocks
  } catch {
    return null // best-effort: the cache just stays as it was
  }
}
