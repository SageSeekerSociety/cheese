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

function keyOf(topicId: string, author: string | null): string {
  return `${topicId}\u0000${author ?? '*'}`.toString()
}

/** 这个视角上次手上那一窗；没有（没看过、或换了对话）就是 null。 */
export function cachedWindow(topicId: string, author: string | null, limit: number): SiteWindow | null {
  const held = WINDOWS.get(keyOf(topicId, author))
  if (!held || held.blocks.length === 0) return null
  // 只画一页：读者上次往上翻出来的历史不在这里（那是另一件事，翻的时候再读），
  // 免得切回来先铺一屏旧历史、再把位置算错。
  const blocks = held.blocks.slice(-limit)
  return { ...held, blocks, hasMore: held.hasMore || blocks.length < held.blocks.length }
}

/** 把这一窗写回去。只留一页的量：见 `cachedWindow`。 */
export function rememberWindow(topicId: string, author: string | null, window: SiteWindow, limit: number): void {
  if (window.blocks.length === 0) {
    WINDOWS.delete(keyOf(topicId, author))
    return
  }
  WINDOWS.set(keyOf(topicId, author), { ...window, blocks: window.blocks.slice(-limit) })
}

/** 测试和换人登录用：整张表清掉。 */
export function forgetWindows(): void {
  WINDOWS.clear()
}
