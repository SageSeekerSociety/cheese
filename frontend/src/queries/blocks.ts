// 每个房间对话最新的那一段，打开房间时先画它：切回看过的房间第一帧就是上次的样子，
// 不闪空白。
//
// 存的是一个**窗口**，不是整段对话：最新一页，往上翻过的话连同翻出来的那些。
// `hasMore` 说窗口上面还有没有更早的。往上翻、往下读由对话栏自己做（`useChatPaging`），
// 停在最新那一段时把窗口写回这里。
//
// 读最新一页的路有好几条（打开房间的导航、悬停预取、未读变多时的后台预取、对话栏
// 自己），同一个房间同一时刻只有一个请求在路上，后来的跟着它走。
import type { Block } from '@/cx_types'

import { queryOptions } from '@tanstack/vue-query'

import { listBlocks } from '@/api'
import { mergeRefreshedTail, PAGE_SIZE } from '@/lib/blockPaging'
import { queryClient } from '@/lib/queryClient'
import { keys } from '@/queries/keys'

export interface BlockWindow {
  blocks: Block[]
  hasMore: boolean
}

export function newestBlocksQuery(roomId: string) {
  const queryKey = keys.roomNewestBlocks(roomId)
  return queryOptions({
    queryKey,
    queryFn: async (): Promise<BlockWindow> => {
      const page = await listBlocks(roomId, { limit: PAGE_SIZE })
      // `has_more` is a boolean on the wire; a response that leaves it out is
      // "nothing older", not "unknown".
      const fresh = { blocks: page.data, hasMore: !!page.has_more }
      const held = queryClient.getQueryData<BlockWindow>(queryKey)
      // 接上，不是盖掉：往上翻过的那一段要留着。
      return held ? mergeRefreshedTail(held, fresh) : fresh
    },
  })
}

export function cachedWindow(roomId: string): BlockWindow | null {
  return queryClient.getQueryData<BlockWindow>(keys.roomNewestBlocks(roomId)) ?? null
}

export function setCachedWindow(roomId: string, window: BlockWindow): void {
  queryClient.setQueryData(keys.roomNewestBlocks(roomId), window)
}

/** 现在就去问最新一页；已经有一个在路上就等它。 */
export function readNewestBlocks(roomId: string): Promise<BlockWindow> {
  return queryClient.fetchQuery({ ...newestBlocksQuery(roomId), staleTime: 0 })
}

// 后台预取是「顺手做的事」，不是「必须做完的事」。刷新页面时未读话题可能有几十个，
// 一次性全发出去会在同一瞬间占满后端的数据库连接池——被挤出去的不只是这些预取，还有
// 同一时刻用户真正在等的那个请求。所以一次只放两个出去，剩下的排队。
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

/**
 * 在后台把这个房间最新一页取进来，排在预取的队里。`fresh` 时手上那份再新也重取
 * （房间里来了新消息）；否则手上那份还新鲜就不取。失败不说：窗口维持原样。
 */
export async function prefetchNewestBlocks(roomId: string, opts: { fresh?: boolean } = {}): Promise<void> {
  await acquireLane()
  try {
    await queryClient.prefetchQuery(opts.fresh ? { ...newestBlocksQuery(roomId), staleTime: 0 } : newestBlocksQuery(roomId))
  } finally {
    releaseLane()
  }
}
