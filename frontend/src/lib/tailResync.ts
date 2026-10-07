// 断线重连后，读回来的最新一页和屏幕上那一段怎么对上。
//
// 进房间时可以把整段换掉（屏幕上本来就没有这间房的东西）；重连时屏幕上正是这间
// 房，读的人可能正盯着某一行，也可能往上翻了好几页。整段换掉会让滚动位置跳、往上
// 翻过的历史丢掉、每一行重画一遍。所以这里只算出要就地做的几件事：哪几块变了或者
// 是新来的，哪几块在断线期间被撤回了。
//
// 屏幕上那一段只有画得出来的块（useTimeline 的 renders），读回来的一页是原始的，
// 所以两边按时间对齐，不按「第一块的 id」对齐：读回来的一页从它最老那一块的时刻起
// 是完整的，屏幕上比那一刻新、而这一页里没有的块，就是断线期间被撤回的。

import type { Block } from '../cx_types'
import type { BlockWindow } from './blockPaging'

import { ATTACHED } from './blockPaging'
import { reconcile } from './reconcile'

export interface TailResync {
  /** 断线期间来了不止一页：两段之间有一截从没读过，只能整段换成读回来的这一页。 */
  gap: boolean
  /** 新来的、或内容变了的块，按读回来的顺序。 */
  upserts: Block[]
  /** 屏幕上有、断线期间被撤回了的块。 */
  removed: string[]
}

function at(block: Block): number {
  return Date.parse(block.created_at)
}

/** 读回来的这一块和屏幕上那一块内容一样吗。挂件（支线、例行任务那一行）读回来的
 *  块上可能没带，没带就不算变了。 */
function unchanged(before: Block, fresh: Block): boolean {
  const candidate = { ...fresh }
  for (const key of ATTACHED) if (!(key in fresh) && key in before) Object.assign(candidate, { [key]: before[key] })
  return reconcile(before, candidate) === before
}

/**
 * @param shown 屏幕上最新的那一段（停在历史中间时，是背后收着的那段）。
 * @param fresh 刚读回来的最新一页，断线期间的实时帧已经合进去。
 */
export function resyncTail(shown: Block[], fresh: BlockWindow): TailResync {
  const page = fresh.blocks
  if (!page.length) return { gap: false, upserts: [], removed: [] }
  const from = at(page[0])
  // 这一页一直读到了历史开头，或者屏幕上有一块不比它最老那块旧（两段在时间上重叠）：
  // 接得上。否则两段之间可能有没读过的块。
  const meets = !fresh.hasMore || shown.some((block) => at(block) >= from)
  if (!meets) return { gap: true, upserts: page, removed: [] }

  const inPage = new Set(page.map((block) => block.id))
  const known = new Map(shown.map((block) => [block.id, block]))
  return {
    gap: false,
    upserts: page.filter((block) => {
      const before = known.get(block.id)
      return !before || !unchanged(before, block)
    }),
    // 同一时刻的块不算：那一刻可能有几块，这一页恰好只带了其中一部分。
    removed: shown.filter((block) => at(block) > from && !inPage.has(block.id)).map((block) => block.id),
  }
}
