// 文档里 @ / # 菜单的状态：开在哪、列着谁、停在第几行。候选和写进正文的东西在
// lib/docMentionMenu.ts，菜单的样子和对话输入框共用 components/room/MentionMenu.vue。
import type { SuggestionProps } from '@tiptap/suggestion'
import type { Topic } from '../cx_types'
import type { RefItem, RefTrigger } from '../lib/docMentionMenu'
import type { LinkableDocument } from '../lib/docMentionMenu'
import type { MentionPoolEntry } from './useRoomMentionPicker'

import { ref } from 'vue'

import { createRefMenu, documentItems, hashItems, peopleItems, topicItems } from '../lib/docMentionMenu'

/** 浮层贴着光标放的位置。坐标相对 `.doc-editor-wrap`，和文档里别的浮层一样跟着内容滚。 */
export interface CaretMenuPos {
  /** 放在光标下面时给 top，翻到上面时给 bottom（贴住光标那一行往上长，不用知道菜单多高）。 */
  top?: number
  bottom?: number
  left: number
  width: number
  /** 这一侧真正看得见的高度：超出的部分在菜单里滚，不伸到顶栏、键盘或格式条底下去。 */
  maxHeight: number
}

/** 光标上下真正看得见的那一段：可见视口（键盘弹起时它变矮）、外面每一层会裁内容的
 *  容器、跟着页面一起滚时钉在顶上的文档顶栏，和贴在键盘上的格式条，叠出来的那一截。 */
function visibleBand(wrap: HTMLElement, caret: DOMRect): { top: number; bottom: number } {
  const view = window.visualViewport
  let top = view ? view.offsetTop : 0
  let bottom = view ? view.offsetTop + view.height : window.innerHeight
  for (let el = wrap.parentElement; el; el = el.parentElement) {
    if (getComputedStyle(el).overflowY === 'visible') continue
    const r = el.getBoundingClientRect()
    top = Math.max(top, r.top)
    bottom = Math.min(bottom, r.bottom)
  }
  for (const el of document.querySelectorAll('.doc-top')) {
    const bar = el.getBoundingClientRect()
    if (bar.height && bar.bottom <= caret.top && bar.left <= caret.left && caret.left <= bar.right)
      top = Math.max(top, bar.bottom)
  }
  const keys = document.querySelector('.doc-keyboard-bar')?.getBoundingClientRect()
  if (keys && keys.top >= caret.bottom) bottom = Math.min(bottom, keys.top)
  return { top, bottom }
}

/** 下面放得下就放下面，否则上面放得下放上面；两边都不够，放空间大的一边，菜单在
 *  里面滚。给了宽度的，最宽不超过编辑区，靠右时往左收：外层会把伸出编辑区的部分
 *  裁掉，手机上光标又常在行尾。`height` 是菜单想要的高度，只用来挑上下。 */
export function caretMenuPos(
  p: Pick<SuggestionProps<unknown, unknown>, 'editor' | 'clientRect'>,
  height: number,
  width = 0
): CaretMenuPos | null {
  // 从这个编辑器自己往外找：页面上可能同时有不止一份文档（别的面板里还开着一份）。
  const wrap = p.editor.view.dom.closest('.doc-editor-wrap') as HTMLElement | null
  const rect = p.clientRect?.()
  if (!wrap || !rect) return null
  const wr = wrap.getBoundingClientRect()
  const w = Math.min(width, wr.width)
  const left = Math.max(0, Math.min(rect.left - wr.left, wr.width - w))
  const gap = 6
  const band = visibleBand(wrap, rect)
  const below = band.bottom - rect.bottom - gap * 2
  const above = rect.top - band.top - gap * 2
  if (below >= height || below >= above) {
    return { top: rect.bottom - wr.top + gap, left, width: w, maxHeight: Math.max(below, 0) }
  }
  return { bottom: wr.bottom - rect.top + gap, left, width: w, maxHeight: above }
}

/** 菜单一行多高：触屏上两个菜单的行都撑到 44（手指点得中）。只用来挑放在光标上面还是下面。 */
export function menuRowHeight(fine: number): number {
  return window.matchMedia?.('(pointer: coarse)').matches ? 44 : fine
}

// 和 MentionMenu 的 .mention-menu--at 一样宽。
const MENU_WIDTH = 280

export interface RefMenuState extends CaretMenuPos {
  items: RefItem[]
  index: number
}

export function useDocRefMenu(source: {
  people: () => MentionPoolEntry[]
  topics: () => Topic[]
  /** 能引用的文档；不给就只有频道。 */
  documents?: () => LinkableDocument[]
  /** 正在写的这一份：不引用自己。 */
  self?: () => string | null
}) {
  const menu = ref<RefMenuState | null>(null)
  // 最近一次建议插件给的 props：鼠标和键盘挑中都走它的 command()，「@查询」那段字才删得一致。
  let current: SuggestionProps<RefItem, RefItem> | null = null

  function show(p: SuggestionProps<RefItem, RefItem>) {
    current = p
    // 没有候选就不开：# 在正文里很常见（#2423、C#），打一个编号不该弹出一块「暂无匹配」。
    const pos = p.items.length
      ? caretMenuPos(p, Math.min(p.items.length, 9) * menuRowHeight(36) + 58, MENU_WIDTH)
      : null
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
      trigger === '@'
        ? peopleItems(source.people(), query)
        : hashItems(
            topicItems(source.topics(), query),
            documentItems(source.documents?.() ?? [], query, source.self?.() ?? null)
          ),
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
