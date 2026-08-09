// Per-topic timeline cache shared between the chat panel (instant render on
// switch-back — no blank flash) and the unread poll (background refresh, so a
// reply that landed while you were in ANOTHER topic is already in the cache
// when you come back: the last message is there on the very first frame).
import type { Block } from '../cx_types'

import { listBlocks } from '../api'

export const blockCache = new Map<string, Block[]>()

// Dev-only observability hook: lets probe scripts (scripts/probe_flash.py)
// inspect the REAL cache instance — a dynamic import from the console/probe
// can resolve to a second module instance under Vite HMR, which lies.
declare global {
  interface Window {
    __blockCache?: Map<string, Block[]>
  }
}
if (import.meta.env.DEV) window.__blockCache = blockCache

export async function refreshBlockCache(topicId: string): Promise<Block[] | null> {
  try {
    const payload = await listBlocks(topicId)
    blockCache.set(topicId, payload.data)
    return payload.data
  } catch {
    return null // best-effort: the cache just stays as it was
  }
}
