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

// 后台预取是「顺手做的事」，不是「必须做完的事」。刷新页面时未读话题可能有几十
// 个，一次性把它们全发出去会在同一瞬间占满后端的数据库连接池——被挤出去的不只是
// 这些预取，还有同一时刻用户真正在等的那个请求。所以这条队列一次只放两个进去，
// 剩下的排队，没有人在等它们。
const PREFETCH_LANES = 2
let prefetchRunning = 0
const prefetchQueue: (() => void)[] = []

function acquireLane(): Promise<void> {
  if (prefetchRunning < PREFETCH_LANES) {
    prefetchRunning += 1
    return Promise.resolve()
  }
  return new Promise<void>((resolve) => prefetchQueue.push(resolve))
}

function releaseLane(): void {
  const next = prefetchQueue.shift()
  if (next) next()
  else prefetchRunning -= 1
}

// 同一个话题最多一条请求在飞。未读轮询、hover 预取、切回话题这几条路会在同一秒
// 里指向同一个话题，而它们要的是同一样东西——最新那一页。第二条排上去只会多占一
// 条闸道、把别的话题挤到后面，取回来的还是同一页。在飞的那条已经在办这件事了，
// 后来的直接跟着它的结果走。
const inFlight = new Map<string, Promise<Block[] | null>>()

export function refreshBlockCache(topicId: string): Promise<Block[] | null> {
  const running = inFlight.get(topicId)
  if (running) return running
  const started = fetchNewestPage(topicId).finally(() => inFlight.delete(topicId))
  inFlight.set(topicId, started)
  return started
}

async function fetchNewestPage(topicId: string): Promise<Block[] | null> {
  await acquireLane()
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
  } finally {
    releaseLane()
  }
}
