// 文档里 @ / # 菜单的状态：开在哪、列着谁、停在第几行。候选和写进正文的东西在
// lib/docMentionMenu.ts，菜单的样子和对话输入框共用 components/room/MentionMenu.vue。
import type { SuggestionProps } from '@tiptap/suggestion'
import type { Topic } from '../cx_types'
import type { RefItem, RefTrigger } from '../lib/docMentionMenu'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { ref } from 'vue'

import { createRefMenu, peopleItems, topicItems } from '../lib/docMentionMenu'

/** 浮层贴着光标放：坐标相对 `.doc-editor-wrap`，和文档里别的浮层一样跟着内容滚。
 *  下面放不下就翻到光标上方。给了宽度的，最宽不超过编辑区，靠右时往左收：外层会
 *  把伸出编辑区的部分裁掉，手机上光标又常在行尾。 */
export function caretMenuPos(
  clientRect: (() => DOMRect | null) | null | undefined,
  height: number,
  width = 0
): { top: number; left: number; width: number } | null {
  const wrap = document.querySelector('.doc-editor-wrap') as HTMLElement | null
  const rect = clientRect?.()
  if (!wrap || !rect) return null
  const wr = wrap.getBoundingClientRect()
  const w = Math.min(width, wr.width)
  const fitsBelow = rect.bottom + 6 + height <= window.innerHeight
  return {
    top: fitsBelow ? rect.bottom - wr.top + 6 : rect.top - wr.top - height - 6,
    left: Math.max(0, Math.min(rect.left - wr.left, wr.width - w)),
    width: w,
  }
}

// 和 MentionMenu 的 .mention-menu--at 一样宽。
const MENU_WIDTH = 280

export interface RefMenuState {
  items: RefItem[]
  index: number
  top: number
  left: number
  width: number
}

export function useDocRefMenu(source: { people: () => MentionPoolEntry[]; topics: () => Topic[] }) {
  const menu = ref<RefMenuState | null>(null)
  // 最近一次建议插件给的 props：鼠标和键盘挑中都走它的 command()，「@查询」那段字才删得一致。
  let current: SuggestionProps<RefItem, RefItem> | null = null

  function show(p: SuggestionProps<RefItem, RefItem>) {
    current = p
    // 没有候选就不开：# 在正文里很常见（#2423、C#），打一个编号不该弹出一块「暂无匹配」。
    const pos = p.items.length ? caretMenuPos(p.clientRect, Math.min(p.items.length, 7) * 36 + 10, MENU_WIDTH) : null
    menu.value = pos ? { items: p.items, index: 0, ...pos } : null
  }

  function pick(item: RefItem) {
    current?.command(item)
  }

  function hover(index: number) {
    if (menu.value) menu.value.index = index
  }

  function onKeyDown({ event }: { event: KeyboardEvent }): boolean {
    const m = menu.value
    if (!m) return false
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      m.index = (m.index + (event.key === 'ArrowDown' ? 1 : -1) + m.items.length) % m.items.length
      return true
    }
    if (event.key === 'Enter' || event.key === 'Tab') {
      pick(m.items[m.index])
      return true
    }
    return false
  }

  const extension = createRefMenu({
    items: (trigger: RefTrigger, query) =>
      trigger === '@' ? peopleItems(source.people(), query) : topicItems(source.topics(), query),
    onStart: show,
    onUpdate: show,
    onExit: () => {
      menu.value = null
      current = null
    },
    onKeyDown,
  })

  return { menu, extension, pick, hover }
}
