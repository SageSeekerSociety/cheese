/**
 * 对话栏此刻显示时间线的哪一段，以及这一段怎么变。
 *
 * 显示的是一个窗口：最新的一页，往上翻时一页页补上更早的（`hasMore` 说上面还有没有）。
 * 块从哪来——打开房间时读的历史、socket 推来的一帧、自己改完的那一条——是房间壳的事；
 * 这里只管它们怎么落进窗口：同一块不出现两次，改了的原地换掉，撤回的拿走。
 *
 * 取数、缓存、滚动都不在这里。
 */

import type { Block } from '../../../cx_types'
import type { BlockWindow } from '../../../lib/blockPaging'

import { ref } from 'vue'

import { prependOlder } from '../../../lib/blockPaging'

export function useTimeline() {
  /** 显示着的块，从旧到新。 */
  const messages = ref<Block[]>([])
  /** 窗口上面还有更早的块。 */
  const hasMore = ref(false)

  /** 整个换成这一段。 */
  function show(window: BlockWindow) {
    messages.value = window.blocks
    hasMore.value = window.hasMore
  }

  /** 此刻的窗口，存进缓存用。 */
  function current(): BlockWindow {
    return { blocks: messages.value, hasMore: hasMore.value }
  }

  function find(id: string): Block | undefined {
    return messages.value.find((m) => m.id === id)
  }

  /** 新来的一块接到末尾。已经在了就不动，返回 false。 */
  function append(block: Block): boolean {
    if (messages.value.some((m) => m.id === block.id)) return false
    messages.value.push(block)
    return true
  }

  /** 已经显示着的一块变了：原地换掉。不在窗口里就什么也不做。 */
  function replace(block: Block) {
    const at = messages.value.findIndex((m) => m.id === block.id)
    if (at >= 0) messages.value.splice(at, 1, block)
  }

  function remove(id: string) {
    messages.value = messages.value.filter((m) => m.id !== id)
  }

  /** 往上翻到的那一页拼到顶上。 */
  function prepend(older: Block[], more: boolean): BlockWindow {
    const next = prependOlder(current(), older, more)
    show(next)
    return next
  }

  return { messages, hasMore, show, current, find, append, replace, remove, prepend }
}
