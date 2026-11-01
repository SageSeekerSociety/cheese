// 「现场」打开时先画的那一页：每个视角手上的窗口存在这里，切走再回来第一帧就是
// 上次的样子，不用对着骨架屏等一次往返。
//
// 存的是一个**窗口**，和对话栏那一份（`query/blocks.ts`）同一个想法，但不住在
// TanStack 缓存里：现场那一窗不是「一份服务端资源」，而是读者翻到哪里、窗口裁过没有
// 这些只属于这一格的本地状态，写进全局缓存会被别处的作废一起带走。所以这里是一张
// 模块级的表，进程活着就在，刷新页面就没了——那正是我们要的：缓存只省一次往返，
// 不假装是权威。
//
// 一条对话一个视角各一份：读者切到「只看某个队友」再切回来，两边都不能串。
import type { Block } from '../cx_types'

export interface SiteWindow {
  /** 手上这一窗，从旧到新。 */
  blocks: Block[]
  /** 上面还有更早的，往回翻的下一个游标就是 `oldestCursor`。 */
  hasMore: boolean
  oldestCursor: string | null
  /** 接着问「从这里往后有什么」的游标：这一窗最新那一条的。 */
  newestCursor: string | null
}

const WINDOWS = new Map<string, SiteWindow>()

/** 这条记录自己的游标（后端每条都带着，往回翻就从这个值接着读）。 */
export function cursorOf(block: Block): string | null {
  const cursor = (block.meta as { cursor?: unknown } | null | undefined)?.cursor
  return typeof cursor === 'string' ? cursor : null
}

function keyOf(topicId: string, author: string | null): string {
  return `${topicId}\u0000${author ?? '*'}`.toString()
}

/** 这个视角上次手上那一窗；没有（没看过、或换了对话）就是 null。
 *
 *  打开只画一页：更早的那些（上次往上翻出来的、预取补的那两页）不铺上屏面，否则
 *  切回来先铺一屏旧历史、再把读者放在错的地方。但**游标要说真话**：画出来这一页的
 *  头一条就是往回翻的起点，上面还有没有也照实说——不然读者往上翻会漏掉中间那几页。 */
export function cachedWindow(topicId: string, author: string | null, limit: number): SiteWindow | null {
  const held = WINDOWS.get(keyOf(topicId, author))
  if (!held || held.blocks.length === 0) return null
  const dropped = Math.max(0, held.blocks.length - limit)
  const blocks = held.blocks.slice(dropped)
  // 裁过之后往上翻的起点要跟着挪：画出来这一页的头一条。但从这一条**本身**取不到
  // 游标时（socket 换上来的平台记账行不带 `cursor`）不能就这么空着——那等于告诉
  // 「上面没有了」，往上翻就整场失效。退一步取这一窗里最早那条**带着游标**的：
  // 它最多让人多读一遍中间那几条（按 id 去重），不会漏。
  let oldest = held.oldestCursor
  if (dropped > 0) {
    oldest = blocks.map(cursorOf).find((c) => c !== null) ?? null
    if (oldest === null) {
      // 画出来的这一页里一条带游标的都没有：这批行压根不能定位，退到整页重读。
      return null
    }
  }
  return { ...held, blocks, hasMore: held.hasMore || dropped > 0, oldestCursor: oldest }
}

/** 把这一窗写回去。整窗留着（到窗口封顶为止）：往上翻过的那几页也在，回来接着翻
 *  就不用再读一次；`cachedWindow` 只画最后一页。 */
export function rememberWindow(topicId: string, author: string | null, window: SiteWindow, limit: number): void {
  if (window.blocks.length === 0) {
    WINDOWS.delete(keyOf(topicId, author))
    return
  }
  const kept = window.blocks.slice(-Math.max(limit * 3, limit))
  WINDOWS.set(keyOf(topicId, author), { ...window, blocks: kept })
}

/** 测试和换人登录用：整张表清掉。 */
export function forgetWindows(): void {
  WINDOWS.clear()
}
