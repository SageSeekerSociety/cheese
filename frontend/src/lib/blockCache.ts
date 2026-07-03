// Per-topic timeline cache shared between the chat panel (instant render on
// switch-back — no blank flash) and the unread poll (background refresh, so a
// reply that landed while you were in ANOTHER topic is already in the cache
// when you come back: the last message is there on the very first frame).
import { listBlocks } from '../api'
import type { Block } from '../types'

export const blockCache = new Map<string, Block[]>()

export async function refreshBlockCache(topicId: string): Promise<Block[] | null> {
  try {
    const payload = await listBlocks(topicId)
    blockCache.set(topicId, payload.data)
    return payload.data
  } catch {
    return null // best-effort: the cache just stays as it was
  }
}
