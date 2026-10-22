// 一次 `cheese_ask` 问的几道题在页面上是一件事：它们共用一条通知（题上的
// `meta.notice_id`），所以按它分组——第一道题是这一组的头，其余几道挂在它下面。
//
// 判据全是纯函数，组件只负责画：哪几张卡是一组、组里每道题的题签、这道题能不能多选。
// 单题（组里只有它自己）不分组，照旧的卡片长。
import type { Block } from '../cx_types'

import { askOptions } from './blockDisplay'

/** 这道题所属的那一组（`meta.notice_id`）；没有就是单题，自成一组。 */
export function askGroupKey(block: Block): string | null {
  const notice = (block.meta as Record<string, unknown> | null)?.notice_id
  return typeof notice === 'string' && notice ? notice : null
}

/** 题签上的短标签；提问时没给就是空串，画的时候用题号顶上。 */
export function askHeader(block: Block): string {
  const header = (block.meta as Record<string, unknown> | null)?.header
  return typeof header === 'string' ? header : ''
}

/** 这道题可以选多项。缺省是单选。 */
export function askMultiSelect(block: Block): boolean {
  return (block.meta as Record<string, unknown> | null)?.multi_select === true
}

/**
 * 把连着的一串块按提问组切开：返回每一组的头块，和「头块 id → 这一组的每道题」。
 *
 * 只有一组（两三道题挨着、还带得走选项）才算组；单独一道题不进表，照单题的卡片画。
 * 同一组中间插了别的消息就当两组——多问一次就是新的一组，页面不替它们连线。
 */
export function groupAskBlocks(blocks: Block[]): {
  heads: Set<string>
  members: Map<string, Block[]>
  hidden: Set<string>
} {
  const heads = new Set<string>()
  const members = new Map<string, Block[]>()
  const hidden = new Set<string>()

  let index = 0
  while (index < blocks.length) {
    const head = blocks[index]!
    if (!askOptions(head)) {
      index += 1
      continue
    }
    const key = askGroupKey(head)
    const run: Block[] = [head]
    let next = index + 1
    // 同一条通知的题排在一起；中间插了别的块就断。
    while (key && next < blocks.length && askGroupKey(blocks[next]!) === key && askOptions(blocks[next]!)) {
      run.push(blocks[next]!)
      next += 1
    }
    if (run.length > 1) {
      heads.add(head.id)
      members.set(head.id, run)
      for (const row of run.slice(1)) hidden.add(row.id)
    }
    index = next
  }
  return { heads, members, hidden }
}
